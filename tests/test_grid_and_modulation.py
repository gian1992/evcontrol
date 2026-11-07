#!/usr/bin/env python3
"""Regression tests for the night of 2026-11-06.

That night the car charged at 23 A when 13 A was what the deadline asked for,
finished two and a half hours early, dropped out of CRITICAL as a result, met
the "day is over" stop rule, and started again — eight times. It also spent
forty seconds at 6760 W against a 6000 W budget when a 1.5 kW load started
mid-session.

Every number below is taken from the recorder for that night, so these fail
against the old behaviour and pass against the new.
"""

from datetime import datetime, timedelta

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
    calculate_available_current,
    grid_import_cap_amps,
)
from smart_charging import (  # noqa: E402
    SUN_DARK,
    URGENCY_CRITICAL,
    ChargeNeed,
    ForecastSignals,
    PowerSignals,
    SmartConfig,
    SmartState,
    evaluate_smart_charge,
)

# ── the actual state at 01:00 that night ────────────────────────────────────
EV_SOC = 43.0
TARGET_SOC = 80.0
CAPACITY = 42.0
HOURS_LEFT = 21588 / 3600.0      # 5.997 h to the 06:59 deadline
VOLTAGE = 226.0
HOUSE_W = 200.0
BASE_CURRENT = 23.0              # NORMAL mode's base, which is what leaked in
CHARGER_MIN = 6.0
GRID_LIMIT_W = 6000.0


def auto_eco(**over):
    """calculate_available_current with that night's arguments."""
    kwargs = dict(
        surplus=-HOUSE_W,
        battery_power=0.0,              # home battery at 17%, below its floor
        grid_max_power=GRID_LIMIT_W / 1000.0,
        battery_soc=17.0,
        battery_start_charge_soc=80.0,
        voltage=VOLTAGE,
        charger_min_current=BASE_CURRENT,   # the caller really does pass this
        charger_max_current=32.0,
        mode="auto-eco",
        battery_min_soc=20.0,
        house_consumption=HOUSE_W,
        safety_margin_watts=1500.0,
        ev_soc=EV_SOC,
        ev_battery_capacity_kwh=CAPACITY,
        seconds_until_auto_target=int(HOURS_LEFT * 3600),
        automatic_charge_current=15.0,
        ev_charging_speed_percent_per_hour=7.0,   # it was charging, so speed > 0
        target_soc=TARGET_SOC,
    )
    kwargs.update(over)
    return calculate_available_current(**kwargs)


def test_modulated_current_is_not_floored_by_normal_mode():
    """The deadline wanted 13 A. NORMAL's 23 A base must not raise it."""
    print("\n=== Corrente modulata, non la base di NORMAL ===")

    energy = CAPACITY * (TARGET_SOC - EV_SOC) / 100.0
    expected = energy / HOURS_LEFT / 0.90 * 1000.0 / VOLTAGE
    print("  serve: %.1f kWh in %.2f h -> %.1f A" % (energy, HOURS_LEFT, expected))

    amps, reason = auto_eco(charger_floor_current=CHARGER_MIN)
    print("  esito: %.1fA" % amps)
    assert abs(amps - expected) < 1.5, (amps, expected, reason)
    assert amps < 16.0, "23 A era il bug: %r" % (reason,)

    # The night itself, pinned so the regression is unmistakable: no floor given,
    # so it falls back to NORMAL's base, and the old 500 W margin left the grid
    # cap at 23 A too — both roads led to the same wrong answer.
    old_amps, _ = auto_eco(safety_margin_watts=500.0)
    assert old_amps == 23.0, old_amps
    print("  com'era quella notte: %.0fA" % old_amps)
    print("[OK] la modulazione sopravvive alla manopola di NORMAL")


def test_current_tracks_how_much_is_actually_missing():
    """Less to put in means less current, all the way down to the charger floor."""
    print("\n=== La corrente segue quanto manca ===")

    # Two hours left keeps all three inside the critical window, and the grid
    # limit is lifted out of the way so what is being compared is the modulation
    # itself rather than the cap or the start decision.
    seen = []
    for soc in (55.0, 60.0, 65.0):
        amps, _ = auto_eco(ev_soc=soc,
                           seconds_until_auto_target=int(2.0 * 3600),
                           grid_max_power=20.0,
                           charger_floor_current=CHARGER_MIN)
        seen.append(amps)
        need_kwh = CAPACITY * (TARGET_SOC - soc) / 100.0
        print("  dal %2.0f%% all'80%% (%.1f kWh in 2h): %4.1fA" % (soc, need_kwh, amps))

    assert seen == sorted(seen, reverse=True), seen
    assert min(seen) >= CHARGER_MIN, seen
    assert max(seen) <= 32.0, seen
    print("[OK] monotona nel fabbisogno, dentro i limiti della colonnina")


def test_grid_cap_absorbs_a_water_heater():
    """04:26 that night: house jumps 184 W -> 1663 W mid-session."""
    print("\n=== Tetto di rete con lo scaldabagno ===")

    quiet, head_q = grid_import_cap_amps(
        grid_limit_w=GRID_LIMIT_W, margin_w=1500.0, house_w=184.0,
        pv_w=0.0, battery_signed_w=0.0, voltage=VOLTAGE, charger_min_a=CHARGER_MIN)
    spike, head_s = grid_import_cap_amps(
        grid_limit_w=GRID_LIMIT_W, margin_w=1500.0, house_w=1663.0,
        pv_w=0.0, battery_signed_w=0.0, voltage=VOLTAGE, charger_min_a=CHARGER_MIN)

    print("  casa  184W -> tetto %2.0fA (%.0fW per l'auto)" % (quiet, head_q))
    print("  casa 1663W -> tetto %2.0fA (%.0fW per l'auto)" % (spike, head_s))

    # The point of the margin: even the instant the load starts, before the loop
    # has reacted at all, the total stays inside the contract.
    worst_case_w = 184.0 + quiet * VOLTAGE + (1663.0 - 184.0)
    print("  peggior caso prima di reagire: %.0fW su %.0fW" % (worst_case_w, GRID_LIMIT_W))
    assert worst_case_w <= GRID_LIMIT_W + 1, worst_case_w

    # And once it has reacted, the margin is restored.
    assert 1663.0 + spike * VOLTAGE <= GRID_LIMIT_W - 1500.0 + 1
    print("[OK] il margine assorbe l'avvio, poi il tetto lo ripristina")


def test_grid_cap_counts_imports_not_consumption():
    """Sun on the roof is supply: it must widen the budget, not narrow it."""
    print("\n=== Il budget è sull'import, non sui consumi ===")

    night, _ = grid_import_cap_amps(
        grid_limit_w=GRID_LIMIT_W, margin_w=1500.0, house_w=1000.0,
        pv_w=0.0, battery_signed_w=0.0, voltage=VOLTAGE, charger_min_a=CHARGER_MIN)
    sunny, _ = grid_import_cap_amps(
        grid_limit_w=GRID_LIMIT_W, margin_w=1500.0, house_w=1000.0,
        pv_w=5000.0, battery_signed_w=0.0, voltage=VOLTAGE, charger_min_a=CHARGER_MIN)
    charging_batt, _ = grid_import_cap_amps(
        grid_limit_w=GRID_LIMIT_W, margin_w=1500.0, house_w=1000.0,
        pv_w=0.0, battery_signed_w=2000.0, voltage=VOLTAGE, charger_min_a=CHARGER_MIN)

    print("  notte          %2.0fA" % night)
    print("  con 5kW di PV  %2.0fA" % sunny)
    print("  batteria che carica (2kW)  %2.0fA" % charging_batt)
    assert sunny > night, (sunny, night)
    assert charging_batt < night, (charging_batt, night)
    print("[OK] il PV allarga il budget, la batteria in carica lo stringe")


def test_grid_cap_collapses_below_the_charger_minimum():
    """No point closing the contactor on a current the car ignores."""
    print("\n=== Sotto il minimo si va a zero ===")

    cap, head = grid_import_cap_amps(
        grid_limit_w=GRID_LIMIT_W, margin_w=1500.0, house_w=4200.0,
        pv_w=0.0, battery_signed_w=0.0, voltage=VOLTAGE, charger_min_a=CHARGER_MIN)
    print("  casa 4200W -> %.0fA (%.0fW, sotto i %.0fA minimi)" % (cap, head, CHARGER_MIN))
    assert cap == 0.0, cap
    print("[OK] zero, non un'elemosina di ampere")


# ── the latch ───────────────────────────────────────────────────────────────

def night_signals():
    return PowerSignals(pv_w=0.0, house_w=HOUSE_W, ev_w=0.0, battery_w=0.0,
                        battery_soc=17.0, voltage=VOLTAGE, grid_w=None)


def cfg():
    return SmartConfig.from_config({"charger_min_current": 6, "charger_max_current": 32})


def test_critical_latches_through_the_night():
    """Slack crossing back over 1.1 must not end a grid session at 2 a.m."""
    print("\n=== Il critico si aggancia ===")

    state = SmartState()
    now = datetime(2026, 11, 7, 1, 0)
    c = cfg()

    # Deliberately tight: slack just under the critical threshold.
    tight = ChargeNeed(ev_soc=43.0, ev_capacity_kwh=CAPACITY, target_soc=TARGET_SOC,
                       seconds_to_target=int(5.0 * 3600), reference_current_a=15.0)
    d = evaluate_smart_charge(now=now, signals=night_signals(),
                              forecast=ForecastSignals(), need=tight, cfg=c,
                              state=state, refresh_rate=20, charging_allowed=True,
                              critical_target_amps=13.0)
    assert d.urgency == URGENCY_CRITICAL, d.urgency
    assert state.critical_latched, "doveva agganciarsi"
    print("  slack stretto -> %s, agganciato" % d.urgency)

    # An hour of charging later slack is comfortable again. Before the latch this
    # is the moment the session ended and the cycling began.
    state.charging = True
    state.session_start = now
    roomy = ChargeNeed(ev_soc=60.0, ev_capacity_kwh=CAPACITY, target_soc=TARGET_SOC,
                       seconds_to_target=int(4.0 * 3600), reference_current_a=15.0)
    d = evaluate_smart_charge(now=now + timedelta(hours=1), signals=night_signals(),
                              forecast=ForecastSignals(), need=roomy, cfg=c,
                              state=state, refresh_rate=20, charging_allowed=True,
                              critical_target_amps=13.0)
    assert d.urgency == URGENCY_CRITICAL, "il latch doveva reggere: %s" % d.urgency
    assert d.amps > 0, "non deve staccare di notte a obiettivo non raggiunto"
    assert d.sun_phase == SUN_DARK
    print("  slack risalito  -> %s, continua a %.0fA" % (d.urgency, d.amps))
    print("[OK] una sessione sola invece di otto")


def test_latch_releases_when_the_target_is_reached():
    print("\n=== Si sgancia a obiettivo raggiunto ===")

    state = SmartState()
    state.critical_latched = True
    state.charging = True
    state.session_start = datetime(2026, 11, 7, 1, 0)
    done = ChargeNeed(ev_soc=80.0, ev_capacity_kwh=CAPACITY, target_soc=TARGET_SOC,
                      seconds_to_target=int(1.0 * 3600), reference_current_a=15.0)
    d = evaluate_smart_charge(now=datetime(2026, 11, 7, 6, 0), signals=night_signals(),
                              forecast=ForecastSignals(), need=done, cfg=cfg(),
                              state=state, refresh_rate=20, charging_allowed=True)
    assert not state.critical_latched, "all'80% non c'è più niente di critico"
    print("  all'80%%: sganciato, stato %s" % d.state_name)
    print("[OK] il latch non sopravvive all'obiettivo")


def test_latch_releases_when_the_sun_comes_back():
    print("\n=== Si sgancia col sole ===")

    state = SmartState()
    state.critical_latched = True
    state.charging = True
    state.session_start = datetime(2026, 11, 7, 6, 0)
    need = ChargeNeed(ev_soc=60.0, ev_capacity_kwh=CAPACITY, target_soc=TARGET_SOC,
                      seconds_to_target=int(8.0 * 3600), reference_current_a=15.0)
    sunny = PowerSignals(pv_w=4000.0, house_w=500.0, ev_w=0.0, battery_w=0.0,
                         battery_soc=80.0, voltage=VOLTAGE, grid_w=None)
    d = evaluate_smart_charge(now=datetime(2026, 11, 7, 10, 0), signals=sunny,
                              forecast=ForecastSignals(), need=need, cfg=cfg(),
                              state=state, refresh_rate=20, charging_allowed=True)
    assert not state.critical_latched, "col sole torna a comandare il solare"
    print("  sole a 4kW: sganciato, %s" % d.state_name)
    print("[OK] di giorno il controllo torna al surplus")


# ── sizing the reserve for supplies other than this one ─────────────────────
# The plugin is going public, so the defaults have to survive contracts the
# author does not have. A 3 kW supply is the common Italian domestic one.

def test_default_margin_leaves_every_supply_able_to_charge():
    from helpers import default_grid_margin_w

    print("\n=== Il margine di default scala col contratto ===")
    print("  %-10s %-9s %-11s %s" % ("contratto", "margine", "budget auto", "tetto"))
    for kw in (3.0, 4.5, 6.0, 10.0):
        limit = kw * 1000.0
        margin = default_grid_margin_w(limit)
        cap, head = grid_import_cap_amps(
            grid_limit_w=limit, margin_w=margin, house_w=300.0, pv_w=0.0,
            battery_signed_w=0.0, voltage=230.0, charger_min_a=CHARGER_MIN)
        print("  %-10s %-9s %-11s %.0f A"
              % ("%.1f kW" % kw, "%.0f W" % margin, "%.0f W" % max(0.0, head), cap))
        assert cap >= CHARGER_MIN, (
            "a %.1f kW il default rende la ricarica impossibile" % kw)

    # The author's own supply keeps the figure chosen deliberately for it.
    assert default_grid_margin_w(6000.0) == 1500.0
    # A flat 1500 W is exactly what breaks the small supply, which is the point.
    broken, _ = grid_import_cap_amps(
        grid_limit_w=3000.0, margin_w=1500.0, house_w=300.0, pv_w=0.0,
        battery_signed_w=0.0, voltage=230.0, charger_min_a=CHARGER_MIN)
    assert broken == 0.0, broken
    print("  (con 1500W fissi, 3 kW darebbe 0 A: ecco perché scala)")
    print("[OK] ogni taglia di contratto riesce a caricare")


def test_an_impossible_reserve_is_refused_at_configuration_time():
    from helpers import validate_control

    print("\n=== Una riserva impossibile viene rifiutata ===")
    bad = {"grid_max_current_amps": 13, "grid_voltage": 230,
           "grid_safety_margin_w": 1500}
    good = {"grid_max_current_amps": 13, "grid_voltage": 230,
            "grid_safety_margin_w": 750}
    assert validate_control(bad) == "margin_too_large", validate_control(bad)
    assert validate_control(good) is None, validate_control(good)
    print("  3 kW con riserva 1500W -> rifiutata")
    print("  3 kW con riserva  750W -> accettata")
    print("[OK] l'errore arriva mentre c'è qualcuno a leggerlo")


if __name__ == "__main__":
    print("=" * 62)
    print("REGRESSIONE NOTTE 2026-11-06")
    print("=" * 62)

    test_modulated_current_is_not_floored_by_normal_mode()
    test_current_tracks_how_much_is_actually_missing()
    test_grid_cap_absorbs_a_water_heater()
    test_grid_cap_counts_imports_not_consumption()
    test_grid_cap_collapses_below_the_charger_minimum()
    test_critical_latches_through_the_night()
    test_latch_releases_when_the_target_is_reached()
    test_latch_releases_when_the_sun_comes_back()
    test_default_margin_leaves_every_supply_able_to_charge()
    test_an_impossible_reserve_is_refused_at_configuration_time()

    print("\n" + "=" * 62)
    print("[OK] TUTTI I TEST PASSATI")
    print("=" * 62)