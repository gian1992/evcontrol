"""The deadline plan, and the tariff band it tries to fit inside.

Pinned to the night of 2026-11-17, when the car sat at the charger minimum from
19:49 while the display claimed it was keeping pace with a 07:00 deadline. At
6 A it would have arrived at 13:19 -- six hours late -- and the only thing that
would have saved it was a separate branch opening at 23:42 and starting over.

The numbers below are that car: a 42 kWh pack at 52%, 225.5 V at the meter, a
15 A reference, F3 from 22:00 to 07:00.
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "custom_components", "evcontrol"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from helpers import (  # noqa: E402
    calculate_charge_time_hours,
    hours_until_band_open,
    plan_grid_deadline,
)

VOLTAGE = 225.5
CAPACITY = 42.0
REFERENCE = 15.0
CHARGER_MIN = 6.0
CHARGER_MAX = 32.0
BAND_START = 22.0
BAND_END = 7.0


def _plan(now_hour, hours_left, ev_soc, target_soc=100.0, band=True, **over):
    """The plan as the coordinator asks for it, including its reference hours."""
    energy = CAPACITY * max(0.0, target_soc - ev_soc) / 100.0
    kwargs = dict(
        hours_until_deadline=hours_left,
        energy_needed_kwh=energy,
        voltage=VOLTAGE,
        reference_current=REFERENCE,
        charger_min_current=CHARGER_MIN,
        charger_max_current=CHARGER_MAX,
        now_hour=now_hour,
        band_start_hour=BAND_START if band else None,
        band_end_hour=BAND_END if band else None,
        # The house battery is deliberately not in here: see the comment at the
        # call site in coordinator.py.
        hours_at_reference=calculate_charge_time_hours(
            ev_soc, target_soc, CAPACITY, REFERENCE, VOLTAGE, 100.0
        ),
    )
    kwargs.update(over)
    return plan_grid_deadline(**kwargs)


def test_band_window_arithmetic():
    """A window that crosses midnight is the normal shape for a night rate."""
    print("\n=== La finestra attraversa la mezzanotte ===")

    cases = [
        (20.8, 1.2, "le 20:48, prima"),
        (22.0, 0.0, "le 22:00, apre"),
        (23.5, 0.0, "le 23:30, dentro"),
        (3.0, 0.0, "le 03:00, dentro"),
        (6.9, 0.0, "le 06:54, ancora dentro"),
        (7.0, 15.0, "le 07:00, chiusa"),
        (12.0, 10.0, "mezzogiorno"),
    ]
    for now, expected, label in cases:
        got = hours_until_band_open(now, BAND_START, BAND_END)
        print("  %-24s attesa %5.1f h" % (label, got))
        assert abs(got - expected) < 0.01, (label, got, expected)

    # Degenerate settings must not lock charging out forever.
    assert hours_until_band_open(13.0, 9.0, 9.0) == 0.0
    print("[OK] finestra coerente, anche quando e' tutto il giorno")


def test_the_night_of_2026_11_17():
    """The night itself: wait for 22:00, then a current that actually arrives."""
    print("\n=== La notte del 17/11, rifatta ===")

    charge_now, pace, start_in, why = _plan(20.8, 10.17, 52.0)
    print("  alle 20:48 -> carico=%s, fra %.1f h, a %.1f A" % (charge_now, start_in, pace))
    print("     %s" % why)
    assert not charge_now, "a 1 h dalla fascia non c'e' motivo di partire"
    assert abs(start_in - 1.2) < 0.05, start_in
    # The whole point: not the 6 A it actually sat at.
    assert pace > 10.0, "il passo e' ancora il minimo del caricatore: %.1f A" % pace
    assert pace <= REFERENCE + 0.1, "oltre la baseline senza bisogno: %.1f A" % pace

    charge_now, pace_22, _, why = _plan(22.0, 9.0, 52.0)
    print("  alle 22:00 -> carico=%s, a %.1f A" % (charge_now, pace_22))
    print("     %s" % why)
    assert charge_now, "la fascia e' aperta, deve partire"
    assert abs(pace_22 - pace) < 0.2, "il passo non deve saltare all'apertura"

    # Enough to arrive: the pace must beat the plain energy-over-time figure,
    # which is the floor below which the deadline is missed outright.
    wall_kwh = CAPACITY * 0.48 / 0.90
    bare_minimum = wall_kwh / 9.0 * 1000.0 / VOLTAGE
    print("  minimo per arrivare: %.1f A, passo scelto: %.1f A" % (bare_minimum, pace_22))
    assert pace_22 >= bare_minimum, (pace_22, bare_minimum)


def test_a_car_too_empty_starts_before_the_band():
    """"Se è troppo scarica meglio prima": the deadline outranks the tariff."""
    print("\n=== Troppo scarica: si parte prima della fascia ===")

    for soc in (30.0, 10.0):
        charge_now, pace, start_in, why = _plan(20.8, 10.17, soc)
        print("  SOC %2.0f%% -> carico=%s a %.1f A" % (soc, charge_now, pace))
        print("     %s" % why)
        assert charge_now, "con il SOC al %.0f%% la fascia non basta" % soc
        assert start_in == 0.0
        # The expensive leg is held to the baseline: the band, which may then run
        # above it, takes the rest.
        assert pace <= REFERENCE + 0.01, "picco in F2 oltre la baseline: %.1f A" % pace
    print("[OK] la scadenza batte la tariffa, ma al massimo alla baseline")


def test_a_small_shortfall_waits_instead_of_trickling():
    """Running the charger at its floor to save a trickle is not worth day rate."""
    print("\n=== Ammanco piccolo: meglio aspettare ===")

    # Five hours before the band, a shortfall that would need under 6 A to cover.
    charge_now, pace, start_in, why = _plan(17.0, 14.0, 34.0)
    print("  alle 17:00, SOC 34%% -> carico=%s, fra %s h, a %.1f A"
          % (charge_now, ("%.1f" % start_in) if start_in is not None else "-", pace))
    print("     %s" % why)
    if not charge_now:
        assert pace > REFERENCE, (
            "se aspetta, la fascia deve poi andare sopra la baseline: %.1f A" % pace
        )
        print("[OK] aspetta e recupera in fascia, sopra la baseline")
    else:
        assert pace >= CHARGER_MIN
        print("[OK] parte, e il tratto caro resta al minimo utile")


def test_falling_behind_raises_the_pace():
    """The pace is recomputed, so lost time is spent out of the margin."""
    print("\n=== Se resta indietro, il passo sale ===")

    _, pace_22, _, _ = _plan(22.0, 9.0, 52.0)
    # An hour and a half inside the band with nothing delivered.
    _, pace_late, _, _ = _plan(23.5, 7.5, 52.0)
    print("  alle 22:00 %.1f A -> alle 23:30 ancora al 52%%: %.1f A" % (pace_22, pace_late))
    assert pace_late > pace_22 + 1.0, (pace_22, pace_late)

    # And having got ahead, it comes back down.
    _, pace_ahead, _, _ = _plan(1.0, 6.0, 85.0)
    print("  all'01:00 ma gia' all'85%%: %.1f A" % pace_ahead)
    assert pace_ahead < pace_22, (pace_ahead, pace_22)
    print("[OK] sale se serve, scende se siamo in anticipo")


def test_target_reached_is_not_a_deadline():
    """Nothing left to take: no plan, no charge."""
    print("\n=== Obiettivo raggiunto ===")
    charge_now, pace, start_in, why = _plan(23.0, 8.0, 100.0)
    print("  %s / %.1f A / %s" % (charge_now, pace, why))
    assert not charge_now and pace == 0.0 and start_in is None


def test_without_a_band_the_old_rule_stands():
    """A flat tariff must behave as it did: start as late as the baseline allows."""
    print("\n=== Senza fascia, la regola di prima ===")

    charge_now, pace, start_in, why = _plan(20.8, 10.17, 52.0, band=False)
    print("  carico=%s, fra %.1f h, a %.1f A" % (charge_now, start_in, pace))
    print("     %s" % why)
    assert not charge_now
    # 7.95 h of charge at the reference, plus the margin, inside 10.17 h.
    assert 1.0 < start_in < 2.5, start_in

    # And once that moment arrives, it charges rather than waiting again.
    charge_now, pace, _, _ = _plan(23.8, 7.2, 52.0, band=False)
    print("  piu' tardi: carico=%s a %.1f A" % (charge_now, pace))
    assert charge_now and pace > REFERENCE - 1.0
    print("[OK] nessun cambio di comportamento su tariffa unica")


def test_the_pace_always_arrives():
    """The bug, stated as a property: the plan is always still able to arrive.

    Two things make the naive check ("current >= energy/time") wrong, and both
    are deliberate:

      * At 90% with nine hours left the charge needs 2 A. The charger cannot go
        below 6, so 6 A is the answer and it is plenty.
      * On the leg before the band the current is held to the reference on
        purpose, so it can sit *below* the average the whole charge needs. The
        band then runs above it. Judging that leg on its own instantaneous rate
        would call the cheapest correct plan a failure.

    So the invariant is end-to-end feasibility: what this leg delivers, plus what
    the band can still deliver at the charger's ceiling, covers what is left.
    That is precisely what the old code violated on 17/11 -- 6 A with 22 kWh to
    go and ten hours left, and no later leg that could make it up.
    """
    print("\n=== Qualunque piano scelga, puo' ancora arrivare ===")

    checked = 0
    at_ceiling = 0
    thinnest = None
    for tenth in range(0, 101):
        now = 20.0 + tenth * 0.10          # 20:00 -> 06:00
        now_hour = now % 24.0
        hours_left = 31.0 - now            # to 07:00 tomorrow
        if hours_left <= 0.2:
            continue
        for soc in (20.0, 35.0, 52.0, 70.0, 90.0, 97.0):
            charge_now, pace, _, why = _plan(now_hour, hours_left, soc)
            if not charge_now:
                continue
            checked += 1
            wall_kwh = CAPACITY * (100.0 - soc) / 100.0 / 0.90

            band_wait = hours_until_band_open(now_hour, BAND_START, BAND_END)
            this_leg_h = min(band_wait, hours_left) if band_wait > 0 else hours_left
            band_h = max(0.0, hours_left - this_leg_h)

            delivered = pace * VOLTAGE / 1000.0 * this_leg_h
            still_possible = CHARGER_MAX * VOLTAGE / 1000.0 * band_h

            if CHARGER_MAX * VOLTAGE / 1000.0 * hours_left < wall_kwh:
                # Beyond the charger entirely; no plan could arrive.
                at_ceiling += 1
                continue

            assert delivered + still_possible >= wall_kwh - 0.01, (
                "piano irrecuperabile: %.1f A per %.1f h = %.1f kWh, poi al massimo "
                "%.1f kWh, servono %.1f (SOC %.0f%%, alle %.1f): %s"
                % (pace, this_leg_h, delivered, still_possible, wall_kwh,
                   soc, now_hour, why)
            )
            margin = (delivered + still_possible) / wall_kwh if wall_kwh > 0 else 99.0
            if thinnest is None or margin < thinnest[0]:
                thinnest = (margin, now_hour, soc, pace)

    print("  %d piani verificati, %d oltre le possibilita' del caricatore"
          % (checked, at_ceiling))
    print("  margine piu' sottile: %.2fx (alle %.1f, SOC %.0f%%, a %.1f A)" % thinnest)
    assert thinnest[0] >= 1.0

    # And the in-band leg, where the instantaneous rate *is* the whole story,
    # must carry the charge on its own.
    for soc in (35.0, 52.0, 70.0):
        for now_hour, hours_left in ((22.0, 9.0), (0.5, 6.5), (3.0, 4.0)):
            charge_now, pace, _, why = _plan(now_hour, hours_left, soc)
            assert charge_now, (soc, now_hour, why)
            needed = (CAPACITY * (100.0 - soc) / 100.0 / 0.90
                      / hours_left * 1000.0 / VOLTAGE)
            if needed > CHARGER_MAX:
                continue
            assert pace >= needed - 0.01, (
                "in fascia servono %.1f A, ne da %.1f (SOC %.0f%% alle %.1f): %s"
                % (needed, pace, soc, now_hour, why)
            )
    print("[OK] in fascia il passo basta da solo, e nessun piano arriva in ritardo")


if __name__ == "__main__":
    print("=" * 62)
    print("FASCIA ORARIA E PIANO DI SCADENZA")
    print("=" * 62)
    test_band_window_arithmetic()
    test_the_night_of_2026_11_17()
    test_a_car_too_empty_starts_before_the_band()
    test_a_small_shortfall_waits_instead_of_trickling()
    test_falling_behind_raises_the_pace()
    test_target_reached_is_not_a_deadline()
    test_without_a_band_the_old_rule_stands()
    test_the_pace_always_arrives()
    print()
    print("=" * 62)
    print("[OK] TUTTI I TEST PASSATI")
    print("=" * 62)
