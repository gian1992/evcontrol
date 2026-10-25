#!/usr/bin/env python3
"""Test AUTO mode fallback when EV data is missing."""

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

from helpers import calculate_available_current

def test_auto_fallback():
    """Test AUTO mode fallback when EV SOC is None."""
    print("\n=== Testing AUTO Mode Fallback (Missing EV Data) ===\n")
    
    # Test 1: AUTO with no target schedule
    print("Test 1: AUTO with NO target schedule (ev_soc=None)")
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
        ev_soc=None,  # No MQTT data
        ev_battery_capacity_kwh=None,  # No capacity
        seconds_until_auto_target=None,  # No target schedule
        automatic_charge_current=15.0,
    )
    print(f"  Result: {current_a}A")
    print(f"  Message: {msg}")
    # With neither a deadline nor any EV data there is nothing to plan against, so the
    # planner holds at 0A and says what it is waiting for. This test previously expected
    # a fallback to automatic_charge_current; charging a car whose SOC and deadline are
    # both unknown is the worse of the two behaviours, and the code changed deliberately
    # (see the "can't make decision" branch in helpers.calculate_available_current).
    assert current_a == 0.0, "Should hold at 0A when neither a target nor EV data exist"
    assert "waiting for both" in msg
    print("  [OK] PASS\n")
    
    # Test 2: AUTO with target but plenty of time (should wait with fallback).
    # Without EV data the planner assumes 50% of 45 kWh, which needs ~9.0h at 15A, and
    # adds a fixed 10h advance: anything under ~19.0h to target is already critical.
    # This test used to pass 15h and assert a wait, which never exercised the branch it
    # names -- 15h is inside the window. 24h is comfortably outside it.
    print("Test 2: AUTO with target, PLENTY OF TIME, ev_soc=None")
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
        ev_soc=None,  # No MQTT data
        ev_battery_capacity_kwh=None,  # Use default 45 kWh
        seconds_until_auto_target=24 * 3600,  # 24 hours: beyond the ~19.0h threshold
        automatic_charge_current=15.0,
    )
    print(f"  Result: {current_a}A")
    print(f"  Message: {msg}")
    assert current_a == 0.0, "Should wait when plenty of time even without EV data"
    assert "waiting" in msg
    print("  [OK] PASS\n")
    
    # Test 3: AUTO with target and time critical (should charge with fallback)
    print("Test 3: AUTO with target, TIME CRITICAL, ev_soc=None")
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
        ev_soc=None,  # No MQTT data
        ev_battery_capacity_kwh=None,  # Use default 45 kWh
        seconds_until_auto_target=4 * 3600,  # 4 hours - tight!
        automatic_charge_current=15.0,
    )
    print(f"  Result: {current_a}A")
    print(f"  Message: {msg}")
    assert current_a == 15.0, "Should charge when time is critical even without EV data"
    print("  [OK] PASS\n")
    
    # Test 4: AUTO-ECO with time critical, no solar, ev_soc=None
    print("Test 4: AUTO-ECO TIME CRITICAL, NO SOLAR, ev_soc=None")
    current_a, msg = calculate_available_current(
        surplus=-500.0,  # No surplus
        battery_power=500.0,
        grid_max_power=12.0,
        battery_soc=80.0,
        battery_start_charge_soc=70.0,
        voltage=230.0,
        charger_min_current=6.0,
        charger_max_current=32.0,
        mode="auto-eco",
        ev_soc=None,  # No MQTT data
        ev_battery_capacity_kwh=None,
        seconds_until_auto_target=3 * 3600,  # 3 hours - critical
        automatic_charge_current=15.0,
    )
    print(f"  Result: {current_a}A")
    print(f"  Message: {msg}")
    assert current_a == 15.0, "Should charge from grid when critical even without EV data"
    print("  [OK] PASS\n")
    
    print("="*60)
    print("[OK] ALL FALLBACK TESTS PASSED!")
    print("="*60)

if __name__ == "__main__":
    try:
        test_auto_fallback()
    except AssertionError as e:
        print(f"\n[FAIL] Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FAIL] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
