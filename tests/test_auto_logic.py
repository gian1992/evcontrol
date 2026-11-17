#!/usr/bin/env python3
"""Test script for AUTO/AUTO-ECO modes logic."""

from datetime import datetime, timedelta

import os
import sys

# Resolve the integration relative to this file, so the tests run from a clone
# wherever it sits rather than from one hard-coded checkout.
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "custom_components", "evcontrol"))

# The integration's own messages contain non-ASCII characters. A Windows console
# defaults to cp1252, where printing one raises UnicodeEncodeError from inside the
# print -- which masks whatever the test was actually reporting at the time.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):  # pragma: no cover - very old or exotic stdout
    pass

from helpers import (
    calculate_available_current,
    get_next_target_time,
    calculate_charge_time_hours,
    should_start_auto_charge,
)

def test_get_next_target_time():
    """Test getting next target time."""
    print("\n=== Testing get_next_target_time ===")
    
    # Create a target schedule
    target_schedule = {
        "monday": "10:30",
        "tuesday": None,
        "wednesday": "14:00",
        "thursday": None,
        "friday": "16:45",
        "saturday": None,
        "sunday": "09:00",
    }
    
    # Test 1: Monday at 08:00 (before target at 10:30)
    test_monday_morning = datetime(2025, 1, 13, 8, 0, 0)  # Monday
    seconds, target_time, target_soc = get_next_target_time(target_schedule, test_monday_morning)
    print(f"Monday 08:00 -> Target at 10:30: {seconds}s (~ {seconds/3600:.1f}h), Target time: {target_time}")
    assert seconds is not None and target_time == "10:30", "Should return Monday's target"
    
    # Test 2: Monday at 11:00, 30 minutes past the 10:30 target.
    # A target that has only just passed stays critical for a 30-minute grace window,
    # so a car at 99% still finishes instead of stopping because the next target is a
    # day away. The window is inclusive, and 11:00 sits exactly on its edge.
    test_monday_late = datetime(2025, 1, 13, 11, 0, 0)  # Monday
    seconds, target_time, target_soc = get_next_target_time(target_schedule, test_monday_late)
    print(f"Monday 11:00 -> Within grace window: {seconds}, {target_time}")
    assert seconds == 60 and target_time == "10:30", "Should stay critical inside the grace window"
    
    # Test 2b: Monday at 11:31, past the grace window. The search then moves on to the
    # following days -- Tuesday has no target, so Wednesday's 14:00 is the next one.
    test_monday_later = datetime(2025, 1, 13, 11, 31, 0)  # Monday
    seconds, target_time, target_soc = get_next_target_time(target_schedule, test_monday_later)
    print(f"Monday 11:31 -> Past grace window: {seconds}s, {target_time}")
    assert target_time == "14:00", "Should roll forward to Wednesday once the window closes"
    
    # Test 3: Tuesday has no target of its own, so the next one is Wednesday's.
    # This assertion used to expect None, from when the lookup only considered today.
    test_tuesday = datetime(2025, 1, 14, 8, 0, 0)  # Tuesday
    seconds, target_time, target_soc = get_next_target_time(target_schedule, test_tuesday)
    print(f"Tuesday 08:00 -> Next target: {seconds}s, {target_time}")
    assert target_time == "14:00", "Should look ahead to Wednesday's target"
    assert seconds == 30 * 3600, "Wednesday 14:00 is 30h after Tuesday 08:00"
    
    # Test 4: Wednesday at 13:00 (before target at 14:00)
    test_wednesday = datetime(2025, 1, 15, 13, 0, 0)  # Wednesday
    seconds, target_time, target_soc = get_next_target_time(target_schedule, test_wednesday)
    print(f"Wednesday 13:00 -> Target at 14:00: {seconds}s (~ {seconds/3600:.1f}h), Target time: {target_time}")
    assert seconds is not None and target_time == "14:00", "Should return Wednesday's target"
    
    print("[OK] All get_next_target_time tests passed!")

def test_calculate_charge_time():
    """Test charging time calculation."""
    print("\n=== Testing calculate_charge_time_hours ===")
    
    # Test case: 45 kWh battery, 20A charge, 230V voltage.
    # Wall power = 20A * 230V = 4.6 kW, and 50% of 45 kWh = 22.5 kWh, so the naive
    # answer is 4.89h. The real one is longer, because the estimate models two losses:
    #   / 0.90  charging efficiency -- not all wall power reaches the battery
    #   * 1.20  CC/CV taper -- the BMS throttles near 100%, and the target here is 100%
    # 4.89 / 0.90 * 1.20 = 6.52h. The old bound of "~4.9 hours" predates both.
    time_hours = calculate_charge_time_hours(
        soc_current=50.0,
        soc_target=100.0,
        battery_capacity_kwh=45.0,
        charge_current_amps=20.0,
        voltage=230.0,
    )
    print(f"45 kWh battery, 20A, 230V: 50%->100% takes {time_hours:.2f} hours")
    assert 6.4 < time_hours < 6.7, "Expected ~6.5 hours (efficiency + taper included)"
    
    # Test case: Full battery, no charge needed
    time_hours = calculate_charge_time_hours(
        soc_current=100.0,
        soc_target=100.0,
        battery_capacity_kwh=45.0,
        charge_current_amps=15.0,
        voltage=230.0,
    )
    print(f"Already at 100%: {time_hours:.2f} hours")
    assert time_hours == 0, "Expected 0 hours"
    
    print("[OK] All charge time tests passed!")

def test_should_start_auto_charge():
    """Test AUTO charge start decision."""
    print("\n=== Testing should_start_auto_charge ===")
    
    # Test 1: Plenty of time. Charging 50% of 45 kWh at 15A needs ~8.7h, and the
    # decision adds a fixed 10h advance, so the start threshold sits at ~18.7h. This
    # used to pass 12 hours and assert a wait, which stopped being "plenty" when the
    # advance was introduced; 24h is clear of the threshold.
    should_charge, reason = should_start_auto_charge(
        ev_soc=50.0,
        battery_capacity_kwh=45.0,
        charge_current_amps=15.0,
        voltage=230.0,
        seconds_until_target=24 * 3600,  # 24 hours, beyond the ~18.7h threshold
        automatic_charge_current=15.0,
    )
    print(f"24h until target, SOC 50%: {should_charge} ({reason})")
    assert not should_charge, "Should wait when plenty of time"
    
    # Test 2: Not enough time (3 hours until target)
    should_charge, reason = should_start_auto_charge(
        ev_soc=50.0,
        battery_capacity_kwh=45.0,
        charge_current_amps=15.0,
        voltage=230.0,
        seconds_until_target=3 * 3600,  # 3 hours
        automatic_charge_current=15.0,
    )
    print(f"3h until target, SOC 50%: {should_charge} ({reason})")
    assert should_charge, "Should start when running out of time"
    
    print("[OK] All should_start_auto_charge tests passed!")

def test_calculate_available_current_auto():
    """Test AUTO mode current calculation."""
    print("\n=== Testing calculate_available_current (AUTO mode) ===")
    
    # Test 1: LOW SOC (< 30%) - PHASE 1
    current_a, msg = calculate_available_current(
        surplus=500.0,
        battery_power=500.0,
        grid_max_power=12.0,
        battery_soc=80.0,
        battery_start_charge_soc=70.0,
        voltage=230.0,
        charger_min_current=6.0,
        charger_max_current=32.0,
        mode="auto",
        ev_soc=25.0,  # Below 30%
        ev_battery_capacity_kwh=45.0,
        seconds_until_auto_target=10 * 3600,  # 10 hours
        automatic_charge_current=15.0,
    )
    print(f"AUTO PHASE 1 (EV SOC 25%): {current_a}A - {msg}")
    assert current_a == 15.0, "Should charge at automatic_charge_current"
    
    # Test 2: Enough time - NO CHARGING
    current_a, msg = calculate_available_current(
        surplus=500.0,
        battery_power=500.0,
        grid_max_power=12.0,
        battery_soc=80.0,
        battery_start_charge_soc=70.0,
        voltage=230.0,
        charger_min_current=6.0,
        charger_max_current=32.0,
        mode="auto",
        ev_soc=80.0,
        ev_battery_capacity_kwh=45.0,
        seconds_until_auto_target=15 * 3600,  # 15 hours - plenty of time
        automatic_charge_current=15.0,
    )
    print(f"AUTO PHASE 2 (EV SOC 80%, 15h left): {current_a}A - {msg}")
    assert current_a == 0.0, "Should wait when plenty of time"
    
    # Test 3: Running out of time - CHARGE
    current_a, msg = calculate_available_current(
        surplus=500.0,
        battery_power=500.0,
        grid_max_power=12.0,
        battery_soc=80.0,
        battery_start_charge_soc=70.0,
        voltage=230.0,
        charger_min_current=6.0,
        charger_max_current=32.0,
        mode="auto",
        ev_soc=60.0,
        ev_battery_capacity_kwh=45.0,
        seconds_until_auto_target=4 * 3600,  # 4 hours - not enough time
        automatic_charge_current=15.0,
    )
    print(f"AUTO PHASE 2 (EV SOC 60%, 4h left): {current_a}A - {msg}")
    # 15 A was the old answer, and it was wrong: 45 kWh from 60% to 100% is 20 kWh
    # at the wall, which 15 A cannot deliver in four hours. The current now comes
    # from the deadline, so it is whatever actually arrives on time.
    needed = 45.0 * 0.40 / 0.90 / 4.0 * 1000.0 / 230.0
    assert current_a >= needed - 0.5, (
        "Should charge fast enough to arrive: %.1fA < %.1fA needed" % (current_a, needed)
    )
    
    print("[OK] All AUTO mode tests passed!")

def test_calculate_available_current_auto_eco():
    """Test AUTO-ECO mode current calculation."""
    print("\n=== Testing calculate_available_current (AUTO-ECO mode) ===")
    
    # Test 1: Time critical with solar
    current_a, msg = calculate_available_current(
        surplus=3000.0,  # Good solar
        battery_power=500.0,
        grid_max_power=12.0,
        battery_soc=80.0,
        battery_start_charge_soc=70.0,
        voltage=230.0,
        charger_min_current=6.0,
        charger_max_current=32.0,
        mode="auto-eco",
        ev_soc=60.0,
        ev_battery_capacity_kwh=45.0,
        seconds_until_auto_target=4 * 3600,  # 4 hours - critical
        automatic_charge_current=15.0,
    )
    print(f"AUTO-ECO time critical with solar: {current_a}A - {msg}")
    assert current_a > 10.0, "Should use solar when critical and available"
    
    # Test 2: Time critical without solar
    current_a, msg = calculate_available_current(
        surplus=-1000.0,  # No solar (negative surplus)
        battery_power=500.0,
        grid_max_power=12.0,
        battery_soc=80.0,
        battery_start_charge_soc=70.0,
        voltage=230.0,
        charger_min_current=6.0,
        charger_max_current=32.0,
        mode="auto-eco",
        ev_soc=60.0,
        ev_battery_capacity_kwh=45.0,
        seconds_until_auto_target=4 * 3600,  # 4 hours - critical
        automatic_charge_current=15.0,
    )
    print(f"AUTO-ECO time critical no solar: {current_a}A - {msg}")
    # Same correction as the AUTO case above: a four-hour deadline on 20 kWh
    # is not a 15 A job, and asserting 15 A was asserting the car arrives late.
    needed = 45.0 * 0.40 / 0.90 / 4.0 * 1000.0 / 230.0
    assert current_a >= needed - 0.5, (
        "Should charge fast enough to arrive: %.1fA < %.1fA needed"
        % (current_a, needed)
    )
    
    # Test 3: Not critical, no solar
    current_a, msg = calculate_available_current(
        surplus=-500.0,  # No solar
        battery_power=500.0,
        grid_max_power=12.0,
        battery_soc=80.0,
        battery_start_charge_soc=70.0,
        voltage=230.0,
        charger_min_current=6.0,
        charger_max_current=32.0,
        mode="auto-eco",
        ev_soc=60.0,
        ev_battery_capacity_kwh=45.0,
        seconds_until_auto_target=15 * 3600,  # 15 hours - plenty of time
        automatic_charge_current=15.0,
    )
    print(f"AUTO-ECO not critical no solar: {current_a}A - {msg}")
    assert current_a == 0.0, "Should wait when not critical and no solar"
    
    print("[OK] All AUTO-ECO mode tests passed!")

if __name__ == "__main__":
    print(">> Starting AUTO/AUTO-ECO mode tests...\n")
    
    try:
        test_get_next_target_time()
        test_calculate_charge_time()
        test_should_start_auto_charge()
        test_calculate_available_current_auto()
        test_calculate_available_current_auto_eco()
        
        print("\n" + "="*60)
        print("[OK] ALL TESTS PASSED! ")
        print("="*60)
        
    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAIL] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
