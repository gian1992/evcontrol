#!/usr/bin/env python3
"""Tests for "unblock now".

The button's whole claim is a narrow one: it removes the two waits the planner
imposes on itself, and nothing else. So these tests check both halves of that —
that a blocked start really does go through afterwards, and that a start the
planner was declining on the merits still gets declined.
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

from smart_charging import (  # noqa: E402
    STATE_BLOCKED,
    ChargeNeed,
    ForecastSignals,
    PowerSignals,
    SmartConfig,
    SmartState,
    evaluate_smart_charge,
)

NOON = datetime(2026, 11, 7, 12, 0)
REFRESH = 20


def sunny():
    """Plenty of surplus: 6 kW of sun against 1 kW of house."""
    return PowerSignals(pv_w=6000.0, house_w=1000.0, ev_w=0.0, battery_w=0.0,
                        battery_soc=90.0, voltage=230.0, grid_w=None)


def dark():
    """Night: no production at all."""
    return PowerSignals(pv_w=0.0, house_w=800.0, ev_w=0.0, battery_w=0.0,
                        battery_soc=90.0, voltage=230.0, grid_w=None)


def blank_forecast():
    return ForecastSignals()


def config():
    return SmartConfig.from_config({"charger_min_current": 6, "charger_max_current": 32})


def need():
    return ChargeNeed(ev_soc=40.0, ev_capacity_kwh=45.0, target_soc=80.0)


def run(signals, state, now=NOON):
    return evaluate_smart_charge(
        now=now, signals=signals, forecast=blank_forecast(), need=need(),
        cfg=config(), state=state, refresh_rate=REFRESH, charging_allowed=True,
    )


def unblock(state, cfg=None):
    """What EVControlCoordinator.unblock_now does to the planner state."""
    cfg = cfg or config()
    cleared = []
    if state.last_stop is not None:
        state.last_stop = None
        cleared.append("pausa minima dopo lo stop")
    if state.start_dwell_s < cfg.start_dwell_s:
        state.start_dwell_s = float(cfg.start_dwell_s)
        cleared.append("conferma di avvio")
    return cleared


def test_pause_blocks_then_unblocks():
    """The case from the card: stopped minutes ago, sun is up, start refused."""
    print("\n=== Pausa minima: blocca, poi sblocca ===")

    state = SmartState()
    state.last_stop = NOON - timedelta(minutes=5)   # min_off_min default is 20

    d = run(sunny(), state)
    assert d.state_name == STATE_BLOCKED, d.state_name
    assert "pausa minima" in d.reason, d.reason
    assert d.amps == 0.0
    print("  prima  : %s" % d.reason[:78])

    cleared = unblock(state)
    assert "pausa minima dopo lo stop" in cleared, cleared

    d = run(sunny(), state)
    assert d.state_name != STATE_BLOCKED, d.state_name
    assert d.amps > 0, "dopo lo sblocco, col sole, deve partire"
    print("  dopo   : %s" % d.reason[:78])
    print("[OK] la pausa blocca, lo sblocco la toglie e la carica parte")


def test_unblock_does_not_start_without_sun():
    """The promise that matters: it removes the waiting, not the judgement."""
    print("\n=== Senza sole lo sblocco non fa partire niente ===")

    state = SmartState()
    state.last_stop = NOON - timedelta(minutes=5)

    unblock(state)
    d = run(dark(), state)

    assert d.amps == 0.0, "senza surplus non deve partire: %r" % (d.amps,)
    assert "pausa minima" not in d.reason, d.reason
    print("  esito  : %.1fA — %s" % (d.amps, d.reason[:70]))
    print("[OK] la pausa non compare piu' fra i motivi, ma la carica resta ferma")


def test_unblock_skips_the_start_dwell():
    """A start merely waiting out its confirmation happens on the next cycle."""
    print("\n=== Conferma di avvio ===")

    state = SmartState()
    d = run(sunny(), state)
    # Fresh state with sun: the planner starts counting the confirmation dwell.
    assert d.amps == 0.0 and d.dwell_left_s > 0, (d.amps, d.dwell_left_s)
    print("  prima  : conferma, mancano %ds" % d.dwell_left_s)

    unblock(state)
    d = run(sunny(), state)
    assert d.amps > 0, "la conferma era l'unica cosa che mancava"
    print("  dopo   : parte a %.0fA" % d.amps)
    print("[OK] lo sblocco salta la conferma, non il controllo del surplus")


def test_unblock_is_honest_when_there_is_nothing_to_do():
    """Nothing pending means nothing cleared, so a card can show it as inert."""
    print("\n=== Niente da sbloccare ===")

    state = SmartState()
    state.start_dwell_s = float(config().start_dwell_s)
    assert unblock(state) == [], "non deve dichiarare lavoro che non ha fatto"
    print("[OK] nessun timer attivo -> nessuna azione dichiarata")


def test_stop_budget_is_not_bypassed():
    """Explicitly out of scope: the daily budget is a limit, not a wait."""
    print("\n=== Il budget di stop resta ===")

    cfg = config()
    state = SmartState()
    state.stops_today = cfg.max_stops_per_day
    state.stops_day = NOON.date()

    unblock(state)
    d = run(sunny(), state)
    assert d.state_name == STATE_BLOCKED, d.state_name
    assert "budget stop" in d.reason, d.reason
    print("  esito  : %s" % d.reason[:78])
    print("[OK] lo sblocco non tocca il budget giornaliero, come dichiarato")


if __name__ == "__main__":
    print("=" * 60)
    print("UNBLOCK NOW TEST SUITE")
    print("=" * 60)

    test_pause_blocks_then_unblocks()
    test_unblock_does_not_start_without_sun()
    test_unblock_skips_the_start_dwell()
    test_unblock_is_honest_when_there_is_nothing_to_do()
    test_stop_budget_is_not_bypassed()

    print("\n" + "=" * 60)
    print("[OK] ALL UNBLOCK TESTS PASSED!")
    print("=" * 60)
