#!/usr/bin/env python3
"""Tests for the per-day target SOC.

The point of the feature is that a deadline asking for 80% is a materially easier
deadline than one asking for 100%: less energy, so less time, so the moment at
which the grid has to take over arrives later and the sun gets a longer run at it.
These tests pin that down, plus the two ways it can go wrong -- a schedule written
before the feature existed silently meaning something other than 100%, and a
satisfied deadline still dragging the planner into grid charging.
"""

from datetime import datetime

import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "custom_components", "evcontrol"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover
    pass

from helpers import (  # noqa: E402
    DEFAULT_TARGET_SOC,
    calculate_available_current,
    calculate_charge_time_hours,
    get_next_target_time,
    resolve_target_soc,
)
from smart_charging import ChargeNeed  # noqa: E402


def test_resolve_target_soc():
    """A missing or unusable percentage has to mean 100, never 0."""
    print("\n=== Testing resolve_target_soc ===")

    assert resolve_target_soc(None, "monday") == 100.0
    assert resolve_target_soc({}, "monday") == 100.0
    assert resolve_target_soc({"monday": ""}, "monday") == 100.0
    assert resolve_target_soc({"monday": None}, "monday") == 100.0
    assert resolve_target_soc({"tuesday": 80}, "monday") == 100.0
    assert resolve_target_soc({"monday": "nonsense"}, "monday") == 100.0
    # A zero or negative percentage is not a request to never charge: it is a
    # broken value, and the safe reading of a broken value is "fill it up".
    assert resolve_target_soc({"monday": 0}, "monday") == 100.0
    assert resolve_target_soc({"monday": -10}, "monday") == 100.0

    assert resolve_target_soc({"monday": 80}, "monday") == 80.0
    assert resolve_target_soc({"monday": "80"}, "monday") == 80.0
    assert resolve_target_soc({"monday": 80.0}, "monday") == 80.0
    # Above 100 is clamped rather than rejected.
    assert resolve_target_soc({"monday": 120}, "monday") == 100.0

    print("[OK] defaults, clamping and bad input all resolve sensibly")


def test_schedule_without_soc_still_means_full():
    """A 0.4.x schedule has no percentages at all. It must keep meaning 100%."""
    print("\n=== Testing backward compatibility ===")

    schedule = {"monday": "07:00", "saturday": "09:00"}
    monday_6am = datetime(2025, 1, 6, 6, 0)  # a Monday

    seconds, label, target_soc = get_next_target_time(schedule, monday_6am)
    assert label == "07:00", label
    assert seconds == 3600, seconds
    assert target_soc == DEFAULT_TARGET_SOC, target_soc

    print("[OK] a schedule with no percentages still targets 100%")


def test_next_target_carries_its_own_soc():
    """Each day's percentage travels with that day, not with today's."""
    print("\n=== Testing per-day percentages ===")

    schedule = {"monday": "07:00", "tuesday": "08:00", "saturday": "09:00"}
    soc_schedule = {"monday": 80, "tuesday": 100, "saturday": 60}

    monday_6am = datetime(2025, 1, 6, 6, 0)
    _, label, soc = get_next_target_time(schedule, monday_6am, soc_schedule)
    assert (label, soc) == ("07:00", 80.0), (label, soc)

    # Past Monday's deadline and its grace window: the next target is Tuesday's,
    # and it must bring Tuesday's percentage, not Monday's.
    monday_evening = datetime(2025, 1, 6, 20, 0)
    _, label, soc = get_next_target_time(schedule, monday_evening, soc_schedule)
    assert (label, soc) == ("08:00", 100.0), (label, soc)

    # A day with no deadline looks ahead; Friday finds Saturday's 60%.
    friday = datetime(2025, 1, 10, 12, 0)
    _, label, soc = get_next_target_time(schedule, friday, soc_schedule)
    assert (label, soc) == ("09:00", 60.0), (label, soc)

    print("[OK] the percentage follows the day the deadline belongs to")


def test_grace_window_respects_a_met_target():
    """A deadline that got what it asked for must not force grid charging."""
    print("\n=== Testing the post-deadline grace window ===")

    schedule = {"monday": "07:00"}
    soc_schedule = {"monday": 80}
    just_after = datetime(2025, 1, 6, 7, 10)  # 10 minutes past, inside the window

    # Target missed: the window applies, and 60 s means "critical, charge now".
    seconds, label, soc = get_next_target_time(
        schedule, just_after, soc_schedule, ev_soc=55.0)
    assert (seconds, label, soc) == (60, "07:00", 80.0), (seconds, label, soc)

    # Target met: no urgency. The lookup falls through to next Monday rather than
    # pulling grid power at full tariff for a charge that is finished.
    seconds, label, soc = get_next_target_time(
        schedule, just_after, soc_schedule, ev_soc=80.0)
    assert seconds is not None and seconds > 3600, seconds
    assert soc == 80.0, soc

    # No SOC reading at all: assume the worst and keep the window, which is how
    # it behaved before target SOC existed.
    seconds, _, _ = get_next_target_time(schedule, just_after, soc_schedule)
    assert seconds == 60, seconds

    print("[OK] the window fires on a missed target and stays quiet on a met one")


def test_charge_time_scales_with_the_target():
    """80% is cheaper than 100% by more than the energy difference alone."""
    print("\n=== Testing charge time against the target ===")

    to_full = calculate_charge_time_hours(30.0, 100.0, 45.0, 15.0, 230.0)
    to_80 = calculate_charge_time_hours(30.0, 80.0, 45.0, 15.0, 230.0)

    assert to_80 < to_full, (to_80, to_full)
    # Not merely proportional: a 100% target also carries the CC/CV taper
    # overhead, which an 80% one does not, so the gap is wider than 50/70.
    assert to_80 / to_full < (50.0 / 70.0), (to_80, to_full)
    print(f"  to 100%: {to_full:.2f}h, to 80%: {to_80:.2f}h")

    # Already at or past the target is zero work, not negative work.
    assert calculate_charge_time_hours(85.0, 80.0, 45.0, 15.0, 230.0) == 0.0

    print("[OK] a lower target needs strictly less time, and never negative time")


def test_auto_is_not_critical_when_the_target_is_lower():
    """The headline behaviour: 80% buys hours of extra patience in AUTO."""
    print("\n=== Testing AUTO urgency against the target ===")

    common = dict(
        surplus=-500.0,
        battery_power=500.0,
        grid_max_power=12.0,
        battery_soc=50.0,
        battery_start_charge_soc=70.0,
        voltage=230.0,
        charger_min_current=6.0,
        charger_max_current=32.0,
        mode="auto",
        ev_soc=60.0,
        ev_battery_capacity_kwh=45.0,
        seconds_until_auto_target=4 * 3600,  # four hours to go
        automatic_charge_current=15.0,
    )

    amps_full, msg_full = calculate_available_current(**common)
    assert "CRITICAL" in msg_full, msg_full
    assert amps_full > 0, amps_full
    print(f"  target 100%: {amps_full:.1f}A - {msg_full[:70]}")

    amps_80, msg_80 = calculate_available_current(target_soc=80.0, **common)
    assert "CRITICAL" not in msg_80, msg_80
    assert amps_80 == 0.0, amps_80
    print(f"  target  80%: {amps_80:.1f}A - {msg_80[:70]}")

    print("[OK] the same four hours are critical for 100% and relaxed for 80%")


def test_charge_need_energy_follows_the_target():
    """The smart planner's own view of the work left has to agree."""
    print("\n=== Testing ChargeNeed ===")

    full = ChargeNeed(ev_soc=30.0, ev_capacity_kwh=45.0)
    assert full.target_soc == 100.0
    partial = ChargeNeed(ev_soc=30.0, ev_capacity_kwh=45.0, target_soc=80.0)

    assert partial.energy_needed_kwh() < full.energy_needed_kwh()
    print(f"  to 100%: {full.energy_needed_kwh():.1f} kWh, "
          f"to 80%: {partial.energy_needed_kwh():.1f} kWh")

    # Past the target there is no work left, and so no deadline pressure: slack
    # saturates instead of dividing by something near zero.
    done = ChargeNeed(ev_soc=85.0, ev_capacity_kwh=45.0, target_soc=80.0,
                      seconds_to_target=600)
    assert done.energy_needed_kwh() == 0.0
    assert done.slack(230.0) == 999.0, done.slack(230.0)

    print("[OK] energy and slack both follow the target")


if __name__ == "__main__":
    print("=" * 60)
    print("TARGET SOC TEST SUITE")
    print("=" * 60)

    test_resolve_target_soc()
    test_schedule_without_soc_still_means_full()
    test_next_target_carries_its_own_soc()
    test_grace_window_respects_a_met_target()
    test_charge_time_scales_with_the_target()
    test_auto_is_not_critical_when_the_target_is_lower()
    test_charge_need_energy_follows_the_target()

    print("\n" + "=" * 60)
    print("[OK] ALL TARGET SOC TESTS PASSED!")
    print("=" * 60)
