"""Smart solar-first charge planner for EV Control.

Design priorities, in this order:

1. **Minimise contactor cycles.** The Elecq wallbox pauses charging below 6 A, and
   the car refuses to resume after too many pause/resume cycles without a manual
   reset. A stop is therefore treated as an expensive, budgeted event -- not as a
   free consequence of a cloud passing.
2. **Never export.** If power is leaving the meter while the EV could absorb it,
   that is a control failure.
3. **Protect the home battery in proportion to urgency.** With days of slack before
   the EV deadline the home battery is untouchable and the car only gets genuine
   surplus. As the deadline approaches the battery progressively becomes a source
   like any other.
4. Only then: maximise kWh into the car.

The planner is a pure function of (measurements, forecast, config, state). It holds
no Home Assistant references so it can be reasoned about and tested in isolation.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Deque, Optional, Tuple

_LOGGER = logging.getLogger(__name__)

# AC wall-to-battery efficiency, mirrors helpers.CHARGING_EFFICIENCY.
CHARGING_EFFICIENCY = 0.90

# Above this SOC the home battery is considered full: it self-limits in absorption
# and reserving power for it only produces export.
BATTERY_FULL_SOC = 98.0

# How fast the start confirmation unwinds when the surplus dips below threshold,
# as a multiple of one control cycle. Resetting to zero cost all the accumulated
# confirmation for a single dip; unwinding faster than it winds up (a decay above
# 1) let an air conditioner at roughly half duty hold it at zero indefinitely.
# Half a cycle back for every whole cycle forward asks for "sufficient most of the
# time", which is the right question now that stopping is the expensive half.
DWELL_DECAY = 0.5

# -- Urgency levels ----------------------------------------------------------
URGENCY_RELAXED = "relaxed"    # days of slack: home battery untouchable
URGENCY_NORMAL = "normal"      # comfortable: battery shares a little
URGENCY_TIGHT = "tight"        # deadline approaching: battery shares a lot
URGENCY_CRITICAL = "critical"  # must charge now: grid hybrid, everything allowed

# How willing each urgency level is to let the EV eat into the home battery share.
# 0.0 = battery keeps everything it can absorb, 1.0 = battery keeps only its floor.
_URGENCY_SHARE = {
    URGENCY_RELAXED: 0.0,
    URGENCY_NORMAL: 0.35,
    URGENCY_TIGHT: 0.80,
    URGENCY_CRITICAL: 1.0,
}

# -- PV trend (from the forecast, not from history) --------------------------
TREND_RISING = "rising"
TREND_FLAT = "flat"
TREND_FALLING = "falling"

# -- Sun phase (is the day ending, or is this just weather?) -----------------
#
# The stop threshold on its own cannot tell the difference between "the sun is
# going down" and "a cloud went over" -- both look like production falling below
# a number. Ending a session on the second one costs a contactor cycle for
# nothing. So production below the threshold only ends the day when the sun is
# actually on its way out.
SUN_DAY = "day"          # producing, or a dip the sun will come back from
SUN_SETTING = "setting"  # the day is genuinely ending
SUN_DARK = "dark"        # nothing left at all

# Below this there is no production to speak of, whatever any forecast says, and
# no cloud argument applies: there is simply nothing to hold a session open with.
# Scaled against the stop threshold as well, so a high threshold keeps a
# proportionate floor.
SUN_DARK_W = 300.0
SUN_DARK_FRACTION = 0.2

# Window of measured production kept for the fallback slope, used only when
# Solcast is unavailable. Twenty minutes is long enough that a passing cloud
# does not dominate the fit and short enough to catch the evening decline.
PV_TREND_WINDOW_MIN = 20
PV_TREND_MIN_SPAN_MIN = 8.0
PV_TREND_SHORT_MIN = 5.0
PV_SETTING_SLOPE_W_MIN = -4.0

# How low the sun has to be before a low forecast counts as sunset rather than
# as an overcast afternoon.
SUN_LOW_HOURS_LEFT = 1.5

# -- Planner states, surfaced as a diagnostic sensor -------------------------
STATE_IDLE = "idle"          # not charging, nothing pending
STATE_WAITING = "waiting"    # above threshold, counting the start dwell
STATE_BLOCKED = "blocked"    # would start, but a guard forbids it
STATE_CHARGING = "charging"  # following the surplus
STATE_HOLD = "hold"          # at minimum, home battery covering the deficit
STATE_STOPPING = "stopping"  # deficit persists, counting the stop dwell


SMART_DEFAULTS: dict = {
    "smart_enabled": True,
    "solar_stop_pv_w": 400.0,
    "solar_min_soc": 60.0,
    "smart_batt_floor_w": 500.0,
    "smart_batt_max_reserve_w": 2500.0,
    "smart_export_target_w": 0.0,
    "smart_import_deadband_w": 200.0,
    "smart_start_dwell_s": 180,
    "smart_stop_dwell_s": 300,
    "smart_min_off_min": 20,
    "smart_max_stops_per_day": 3,
    "smart_batt_share_soc_low": 60.0,
    "smart_batt_share_soc_high": 90.0,
    "smart_untouchable_slack": 3.0,
    "smart_tight_slack": 1.5,
    "smart_critical_slack": 1.1,
    "smart_capability_window_min": 15,
    "smart_headroom_ema_s": 60,
    "smart_ramp_up_a": 1.0,
    "smart_ramp_down_a": 2.0,
    "smart_write_deadband_a": 1.0,
    "home_battery_capacity_kwh": 10.0,
}


def _hours(value: float) -> str:
    """One decimal, Italian comma: the reason strings are read by a person."""
    return f"{value:.1f}".replace(".", ",")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _taper(value: float, low: float, high: float) -> float:
    """Linear 0->1 ramp: 0 at or below `low`, 1 at or above `high`."""
    if high <= low:
        return 1.0 if value >= high else 0.0
    return _clamp((value - low) / (high - low), 0.0, 1.0)


@dataclass
class SmartConfig:
    """Tunables for the planner. All of these are exposed in the config flow."""

    enabled: bool = True
    charger_min_current: float = 6.0
    charger_max_current: float = 32.0
    solar_stop_pv_w: float = 400.0
    solar_min_soc: float = 60.0
    batt_floor_w: float = 500.0
    batt_max_reserve_w: float = 2500.0
    export_target_w: float = 0.0
    import_deadband_w: float = 200.0
    start_dwell_s: int = 180
    stop_dwell_s: int = 300
    min_off_min: int = 20
    max_stops_per_day: int = 3
    batt_share_soc_low: float = 60.0
    batt_share_soc_high: float = 90.0
    untouchable_slack: float = 3.0
    tight_slack: float = 1.5
    critical_slack: float = 1.1
    capability_window_min: int = 15
    headroom_ema_s: int = 60
    ramp_up_a: float = 1.0
    ramp_down_a: float = 2.0
    write_deadband_a: float = 1.0
    home_battery_capacity_kwh: float = 10.0

    @classmethod
    def from_config(cls, cfg: dict) -> "SmartConfig":
        """Build from the flat integration config dict, falling back to defaults."""

        def g(key: str):
            value = cfg.get(key)
            return SMART_DEFAULTS[key] if value is None else value

        return cls(
            enabled=bool(g("smart_enabled")),
            charger_min_current=float(cfg.get("charger_min_current", 6)),
            charger_max_current=float(cfg.get("charger_max_current", 32)),
            solar_stop_pv_w=float(g("solar_stop_pv_w")),
            solar_min_soc=float(g("solar_min_soc")),
            batt_floor_w=float(g("smart_batt_floor_w")),
            batt_max_reserve_w=float(g("smart_batt_max_reserve_w")),
            export_target_w=float(g("smart_export_target_w")),
            import_deadband_w=float(g("smart_import_deadband_w")),
            start_dwell_s=int(g("smart_start_dwell_s")),
            stop_dwell_s=int(g("smart_stop_dwell_s")),
            min_off_min=int(g("smart_min_off_min")),
            max_stops_per_day=int(g("smart_max_stops_per_day")),
            batt_share_soc_low=float(g("smart_batt_share_soc_low")),
            batt_share_soc_high=float(g("smart_batt_share_soc_high")),
            untouchable_slack=float(g("smart_untouchable_slack")),
            tight_slack=float(g("smart_tight_slack")),
            critical_slack=float(g("smart_critical_slack")),
            capability_window_min=int(g("smart_capability_window_min")),
            headroom_ema_s=int(g("smart_headroom_ema_s")),
            ramp_up_a=float(g("smart_ramp_up_a")),
            ramp_down_a=float(g("smart_ramp_down_a")),
            write_deadband_a=float(g("smart_write_deadband_a")),
            home_battery_capacity_kwh=float(g("home_battery_capacity_kwh")),
        )


@dataclass
class PowerSignals:
    """Instantaneous measurements for one control cycle."""

    pv_w: float
    house_w: float          # house only, EV excluded
    ev_w: float             # measured EV power (not the setpoint)
    battery_w: float        # positive = charging, negative = discharging
    battery_soc: float
    voltage: float
    grid_w: Optional[float] = None  # positive = import, negative = export

    @property
    def surplus_w(self) -> float:
        """PV minus house. What is left for the battery and the car together."""
        return self.pv_w - self.house_w

    def resolved_grid_w(self) -> float:
        """Grid exchange, measured if available, otherwise from the energy balance.

        PV + import + discharge = house + ev + charge, and with `battery_w` signed
        (+ charging) this collapses to:

            import = house + ev + battery - pv

        Derived this way the value is inherently consistent with the other readings,
        which matters more than owning a dedicated meter: all four terms come from
        the same inverter poll, so there is no cross-sensor latency skew.
        """
        if self.grid_w is not None:
            return self.grid_w
        return self.house_w + self.ev_w + self.battery_w - self.pv_w


@dataclass
class ForecastSignals:
    """Solcast-derived forward view. `valid` is False when Solcast is unavailable."""

    valid: bool = False
    pv_now_w: float = 0.0          # modelled production right now
    pv_30min_w: float = 0.0        # modelled production in 30 minutes
    pv_1h_w: float = 0.0           # modelled production in 1 hour
    remaining_today_kwh: float = 0.0
    tomorrow_kwh: float = 0.0
    daylight_hours_left: float = 0.0
    house_avg_w: float = 0.0       # rolling average house load
    pv_avg_w: float = 0.0          # rolling average PV production

    def bias(self) -> float:
        """Ratio between the real array and the Solcast model.

        Solcast models a nominal array; the real one has its own soiling, shading
        and inverter clipping. Scaling forward values by the observed ratio turns a
        generic model into a calibrated one.

        Deliberately computed from the *averaged* production, never the
        instantaneous one: a cloud overhead right now drives real output down while
        the half-hourly forecast figure stays high, and using that ratio would scale
        the entire forward view down by the depth of a transient. Clamped as a
        second line of defence.
        """
        if not self.valid or self.pv_now_w < 200 or self.pv_avg_w <= 0:
            return 1.0
        return _clamp(self.pv_avg_w / self.pv_now_w, 0.6, 1.6)

    def scaled_30min_w(self) -> float:
        return self.pv_30min_w * self.bias()

    def scaled_1h_w(self) -> float:
        return self.pv_1h_w * self.bias()

    @property
    def trend(self) -> str:
        """Direction of production over the next hour.

        Taken as a ratio inside the forecast itself, so any systematic bias between
        Solcast and the real array cancels out.
        """
        if not self.valid or self.pv_now_w <= 0:
            return TREND_FLAT
        ratio = self.pv_1h_w / max(self.pv_now_w, 1.0)
        if ratio >= 1.15:
            return TREND_RISING
        if ratio <= 0.85:
            return TREND_FALLING
        return TREND_FLAT


@dataclass
class ChargeNeed:
    """What the car still needs, and how long there is to deliver it."""

    ev_soc: Optional[float]
    ev_capacity_kwh: float
    target_soc: float = 100.0
    seconds_to_target: Optional[int] = None
    reference_current_a: float = 15.0

    def energy_needed_kwh(self) -> float:
        if self.ev_soc is None or not self.ev_capacity_kwh:
            return 0.0
        missing = max(0.0, self.target_soc - self.ev_soc)
        return self.ev_capacity_kwh * missing / 100.0 / CHARGING_EFFICIENCY

    def time_needed_h(self, voltage: float) -> float:
        power_kw = self.reference_current_a * voltage / 1000.0
        if power_kw <= 0:
            return 0.0
        return self.energy_needed_kwh() / power_kw

    def slack(self, voltage: float) -> Optional[float]:
        """Time available divided by time required. None when no deadline is set."""
        if self.seconds_to_target is None:
            return None
        needed = self.time_needed_h(voltage)
        if needed <= 0.01:
            return 999.0
        return (self.seconds_to_target / 3600.0) / needed


@dataclass
class SmartState:
    """Mutable state carried across control cycles. Lives on the coordinator."""

    charging: bool = False
    start_dwell_s: float = 0.0
    stop_dwell_s: int = 0
    session_start: Optional[datetime] = None
    last_stop: Optional[datetime] = None
    stops_today: int = 0
    stops_day: Optional[date] = None
    headroom_ema_w: Optional[float] = None
    last_amps: float = 0.0
    batt_samples: Deque[Tuple[datetime, float]] = field(default_factory=deque)
    last_capability_w: float = 0.0
    pv_history: Deque[Tuple[datetime, float]] = field(default_factory=deque)

    def roll_day(self, now: datetime) -> None:
        """Reset the daily stop budget when the date changes."""
        today = now.date()
        if self.stops_day != today:
            self.stops_day = today
            self.stops_today = 0

    def note_battery(
        self, now: datetime, watts: float, window_min: int, trustworthy: bool
    ) -> None:
        """Record a battery power sample, but only when it means anything.

        Battery draw is only evidence of *capability* when the battery is not
        power-starved -- that is, when nothing else was competing for the power it
        declined to take. Concretely: when the EV is off, or when we are exporting
        (so the battery had more available than it wanted).

        Sampling unconditionally creates a ratchet: the car takes more, the battery
        is left with less, the observed "capability" falls, the reserve falls, and
        the car is allowed to take more again. Every 15-minute window the car would
        climb another step and squeeze the battery further, for no reason other
        than a measurement artefact.
        """
        if not trustworthy:
            return
        self.batt_samples.append((now, watts))
        cutoff = now - timedelta(minutes=window_min)
        while self.batt_samples and self.batt_samples[0][0] < cutoff:
            self.batt_samples.popleft()

    def observed_capability_w(self) -> float:
        """Highest charge power the battery accepted in the recent window.

        This answers "how much will it take right now?" -- a question no static
        setting can answer, because acceptance swings between roughly 1 kW when the
        pack is warm and 2.5 kW on a cool morning. The rolling *maximum* rather than
        the instantaneous value is deliberate: even among trustworthy samples, the
        instantaneous one dips with every passing cloud.

        With no fresh trustworthy sample (a long charging session with no export) we
        keep the last value we learned rather than collapsing to zero.
        """
        if self.batt_samples:
            self.last_capability_w = max(w for _, w in self.batt_samples)
        return self.last_capability_w

    def note_pv(self, now: datetime, watts: float) -> None:
        """Keep a timestamped window of production for the fallback slope."""
        self.pv_history.append((now, watts))
        cutoff = now - timedelta(minutes=PV_TREND_WINDOW_MIN)
        while self.pv_history and self.pv_history[0][0] < cutoff:
            self.pv_history.popleft()

    def pv_slope(self, minutes: Optional[float] = None) -> Tuple[float, float]:
        """Least-squares slope of production in W/min, and the span it covers.

        A least-squares fit rather than first-versus-last on purpose: the endpoints
        are exactly what a cloud moves, and a fit over the whole window is not
        fooled by where the cloud happened to land.
        """
        samples = list(self.pv_history)
        if minutes is not None and samples:
            cutoff = samples[-1][0] - timedelta(minutes=minutes)
            samples = [row for row in samples if row[0] >= cutoff]
        if len(samples) < 3:
            return 0.0, 0.0
        t0 = samples[0][0]
        xs = [(t - t0).total_seconds() / 60.0 for t, _ in samples]
        ys = [w for _, w in samples]
        span = xs[-1]
        n = len(xs)
        mean_x = sum(xs) / n
        mean_y = sum(ys) / n
        denom = sum((x - mean_x) ** 2 for x in xs)
        if denom <= 0:
            return 0.0, span
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
        return slope, span

    def update_headroom(self, raw_w: float, refresh_s: int, tau_s: int) -> float:
        """Exponential moving average, so one noisy sample cannot start a session."""
        if tau_s <= 0 or self.headroom_ema_w is None:
            self.headroom_ema_w = raw_w
            return raw_w
        alpha = _clamp(refresh_s / float(tau_s), 0.0, 1.0)
        self.headroom_ema_w = (1 - alpha) * self.headroom_ema_w + alpha * raw_w
        return self.headroom_ema_w



@dataclass
class SmartDecision:
    """Result of one planner evaluation."""

    amps: float
    reason: str
    state_name: str
    urgency: str
    headroom_w: float
    reserve_w: float
    share_factor: float
    capability_w: float
    trend: str
    grid_w: float
    projected_batt_soc: Optional[float] = None
    stops_today: int = 0
    dwell_left_s: int = 0
    slack: Optional[float] = None
    sun_phase: str = SUN_DAY
    sun_reason: str = ""
    pv_slope_w_min: float = 0.0


# -- Urgency -----------------------------------------------------------------

def classify_urgency(
    need: ChargeNeed,
    forecast: ForecastSignals,
    cfg: SmartConfig,
    voltage: float,
) -> Tuple[str, Optional[float], str]:
    """Map remaining slack onto an urgency level.

    A large slack alone is not enough to relax: waiting for sun that the forecast
    says will not arrive is precisely how a solar-first controller ends up pulling
    from the grid at the last minute. So RELAXED additionally requires that the
    forecast can actually cover what the car needs.
    """
    slack = need.slack(voltage)
    if slack is None:
        return URGENCY_NORMAL, None, "nessuna scadenza impostata"

    if slack <= cfg.critical_slack:
        return URGENCY_CRITICAL, slack, f"slack {slack:.2f} <= {cfg.critical_slack}"
    if slack <= cfg.tight_slack:
        return URGENCY_TIGHT, slack, f"slack {slack:.2f}"
    if slack <= cfg.untouchable_slack:
        return URGENCY_NORMAL, slack, f"slack {slack:.2f}"

    solar_kwh = usable_solar_kwh(need, forecast)
    if forecast.valid and solar_kwh < need.energy_needed_kwh():
        return (
            URGENCY_NORMAL,
            slack,
            f"slack {slack:.2f} ampio ma sole previsto insufficiente "
            f"({solar_kwh:.1f} kWh utili < {need.energy_needed_kwh():.1f} kWh necessari)",
        )
    return URGENCY_RELAXED, slack, f"slack {slack:.2f} e sole sufficiente"


def usable_solar_kwh(need: ChargeNeed, forecast: ForecastSignals) -> float:
    """Solar energy expected to be left over for the car before the deadline.

    Counts what remains of today, plus tomorrow when the deadline reaches past
    tomorrow (Solcast publishes two days). The house share is subtracted, because
    production is not surplus.
    """
    if not forecast.valid:
        return 0.0

    house_kw = forecast.house_avg_w / 1000.0
    usable = max(0.0, forecast.remaining_today_kwh - house_kw * forecast.daylight_hours_left)

    if need.seconds_to_target is not None and need.seconds_to_target > 24 * 3600:
        # Assume tomorrow daylight window is close to a full one.
        daylight_tomorrow = max(forecast.daylight_hours_left, 8.0)
        usable += max(0.0, forecast.tomorrow_kwh - house_kw * daylight_tomorrow)
    return usable


# -- Battery reservation -----------------------------------------------------

def battery_reserve(
    signals: PowerSignals,
    forecast: ForecastSignals,
    need: ChargeNeed,
    cfg: SmartConfig,
    state: SmartState,
    urgency: str,
    min_watts: float,
) -> Tuple[float, float, float, Optional[float], str]:
    """Decide how much power the home battery keeps for itself this cycle.

    Returns (reserve_ramp_w, reserve_start_w, share_factor, projected_soc, reason).

    Whether the car runs at all is decided by the sun, not here. What this decides
    is how far **above** the charger minimum it may climb, by interpolating between
    the floor the user guarantees the battery and the most the battery has shown it
    can absorb. Where we sit between them is `share_factor`, driven by whichever of
    three independent reasons is most permissive:

      * SOC       -- a nearly full battery has little left to protect
      * urgency   -- an approaching deadline outranks battery preference
      * forecast  -- if the battery will hit 100% before sunset anyway, holding
                     power back for it only creates export

    We never reserve a *fixed* number of watts for the ramp, because acceptance is
    exactly the quantity a fixed number cannot track: the same pack takes 2.5 kW on
    a cool morning and 1 kW once it is warm.
    """
    if signals.battery_soc >= BATTERY_FULL_SOC:
        return 0.0, 0.0, 1.0, 100.0, "batteria piena: nessuna riserva"

    # The observed maximum is a *lower bound* on true acceptance: once the car is
    # drawing, the battery may be taking less simply because there is less to take.
    # That bias favours the car, which matches the intended everyday behaviour, but
    # it is not safe for RELAXED, where the battery is supposed to win. There we use
    # the user-declared maximum instead, which no measurement can contaminate.
    observed = state.observed_capability_w()
    capability = _clamp(max(observed, cfg.batt_floor_w), 0.0, cfg.batt_max_reserve_w)
    floor = min(cfg.batt_floor_w, capability)

    share_soc = _taper(signals.battery_soc, cfg.batt_share_soc_low, cfg.batt_share_soc_high)
    share_urgency = _URGENCY_SHARE.get(urgency, 0.35)

    projected_soc = project_battery_soc(signals, forecast, need, cfg)
    share_forecast = 1.0 if (projected_soc is not None and projected_soc >= 100.0) else 0.0

    share = max(share_soc, share_urgency, share_forecast)
    reserve_ramp = floor + (capability - floor) * (1.0 - share)

    if urgency == URGENCY_RELAXED and share < 0.99:
        # No floor concession: the car gets only what the battery genuinely cannot
        # absorb. Note this is the *observed* capability, never the declared
        # maximum -- reserving 2.5 kW for a warm pack that will only take 1 kW would
        # push the difference straight out to the grid, which is the one outcome
        # this design refuses regardless of how relaxed the deadline is.
        reserve_start = reserve_ramp
        gate = "batteria ha priorita piena (nessuna fretta), auto solo sugli avanzi veri"
    else:
        reserve_start = floor
        gate = f"avvio consentito lasciando {floor:.0f}W alla batteria"

    # Anti-export outranks battery protection, in every urgency level.
    #
    # When more is arriving than the battery can physically absorb, the excess is
    # spilling to the grid. If that spill is smaller than the charger minimum the
    # car cannot live on it alone -- it has to take the full 6 A and leave the
    # battery correspondingly less. That is worth doing whenever the battery still
    # keeps its floor: the house battery charges a little slower, and nothing at
    # all is given away. Without this the most protective mode would sit and export
    # for hours, which is the one outcome the design exists to prevent.
    spill = signals.surplus_w - capability
    if spill > cfg.export_target_w and (signals.surplus_w - min_watts) >= floor:
        if reserve_start > floor:
            reserve_start = floor
            gate = (
                f"avvio consentito per non immettere ({spill:.0f}W oltre la capacita "
                f"della batteria), lasciandole {floor:.0f}W"
            )

    drivers = []
    if share_soc >= share - 0.01:
        drivers.append(f"SOC {signals.battery_soc:.0f}%")
    if share_urgency >= share - 0.01:
        drivers.append(f"urgenza {urgency}")
    if share_forecast >= share - 0.01:
        drivers.append("batteria satura comunque")
    reason = (
        f"riserva {reserve_ramp:.0f}W (pavimento {floor:.0f}W, capacita osservata "
        f"{observed:.0f}W, condivisione {share:.0%} da "
        f"{' + '.join(drivers) or 'default'}); {gate}"
    )
    return max(0.0, reserve_ramp), max(0.0, reserve_start), share, projected_soc, reason


def project_battery_soc(
    signals: PowerSignals,
    forecast: ForecastSignals,
    need: ChargeNeed,
    cfg: SmartConfig,
) -> Optional[float]:
    """Project the home battery SOC at sunset, given Solcast and the car need.

    Answers the question that actually matters: is this battery going to fill up on
    its own today? If yes, holding power back for it is pointless -- the surplus
    would end up in the grid. If no, the battery is the thing worth protecting,
    because tonight house load has to come from somewhere.
    """
    if not forecast.valid or cfg.home_battery_capacity_kwh <= 0:
        return None

    house_kwh = forecast.house_avg_w / 1000.0 * forecast.daylight_hours_left
    ev_kwh = need.energy_needed_kwh()
    spare_kwh = forecast.remaining_today_kwh - house_kwh - ev_kwh
    delta_soc = spare_kwh / cfg.home_battery_capacity_kwh * 100.0
    return signals.battery_soc + delta_soc


# -- Headroom ----------------------------------------------------------------

def compute_headroom_w(
    signals: PowerSignals,
    reserve_w: float,
    cfg: SmartConfig,
) -> Tuple[float, float]:
    """Power the EV may take right now, and the resolved grid exchange.

    Two independent formulations are evaluated:

        derived  = (PV - house) - reserve - export_target
        measured = export + ev + (battery - reserve) - export_target

    They are algebraically identical when the grid figure comes from the energy
    balance, but they diverge when a real grid meter disagrees with the inverter.
    Taking the lower of the two is conservative on both fronts: it never
    over-commits the car into grid import, and never assumes surplus the meter
    cannot see.

    Note that `+ ev_w` makes this a statement of *total* availability rather than
    marginal availability. That is what keeps the loop stable: the target is
    recomputed from scratch each cycle instead of integrating an error.
    """
    grid_w = signals.resolved_grid_w()

    derived = signals.surplus_w - reserve_w - cfg.export_target_w
    measured = (
        (-grid_w) + signals.ev_w + (signals.battery_w - reserve_w) - cfg.export_target_w
    )
    return min(derived, measured), grid_w


# -- Sun phase ---------------------------------------------------------------

def sun_phase(
    state: SmartState,
    forecast: ForecastSignals,
    cfg: SmartConfig,
    pv_w: float,
) -> Tuple[str, str]:
    """Decide whether production below the threshold means the day is over.

    Three readings, in order of authority:

    0. **The clock.** `sun.sun` knows the sunset time and no cloud can move it.
       While there are hours of daylight left, low production is weather.
    1. **The forecast.** Solcast is time-aware in a way a power reading is not: it
       knows where the sun will be in an hour. If it expects less than the stop
       threshold both half an hour and an hour from here, production is not coming
       back. The catch is that Solcast also models weather, so a forecast that low
       could equally be a thundery afternoon -- hence the second half of the test,
       the sun's own elevation from `sun.sun`. Low forecast *and* the sun nearly
       down is sunset; low forecast with hours of daylight left is a bad sky, and
       a bad sky is exactly the case this planner is built to ride out on the home
       battery rather than to interrupt for.
    2. **The measured slope**, used only when Solcast is missing. Sunset is a
       steady decline over tens of minutes; a cloud is a step down and a step back
       up. Two least-squares fits, over twenty minutes and over five, have to
       agree before the day is called over.

    Returns the phase and a sentence explaining it, which ends up in the status.
    """
    dark_w = max(SUN_DARK_W, cfg.solar_stop_pv_w * SUN_DARK_FRACTION)
    if pv_w <= dark_w:
        return SUN_DARK, (
            f"produzione a {pv_w:.0f}W, sotto {dark_w:.0f}W: non c'è più niente "
            f"da prendere"
        )

    # The clock beats every other signal. `sun.sun` knows when the sun sets and
    # cannot be confused by a cloud, so with hours of daylight still to come a low
    # reading is weather by definition, whatever the forecast or the slope say.
    hours_left = forecast.daylight_hours_left
    if hours_left > SUN_LOW_HOURS_LEFT:
        return SUN_DAY, (
            f"restano {_hours(hours_left)}h di luce, è una nuvola non il tramonto"
        )

    if forecast.valid and forecast.pv_now_w > 0:
        ahead_30 = forecast.scaled_30min_w()
        ahead_1h = forecast.scaled_1h_w()
        if max(ahead_30, ahead_1h) >= cfg.solar_stop_pv_w:
            return SUN_DAY, (
                f"previsti {ahead_30:.0f}W fra 30min e {ahead_1h:.0f}W fra un'ora, "
                f"il sole torna"
            )
        return SUN_SETTING, (
            f"previsti {ahead_30:.0f}W fra 30min e {ahead_1h:.0f}W fra un'ora con "
            f"{_hours(hours_left)}h di luce, il sole sta calando"
        )

    # No Solcast: read the shape of the production curve itself. Both windows have
    # to agree. A cloud is a step -- steep in the long fit for as long as the edge
    # stays inside it, but flat in the last five minutes almost immediately.
    # Sunset is the only thing that keeps both of them pointing down.
    slope, span = state.pv_slope()
    slope_short, _short_span = state.pv_slope(PV_TREND_SHORT_MIN)
    if (
        span >= PV_TREND_MIN_SPAN_MIN
        and slope <= PV_SETTING_SLOPE_W_MIN
        and slope_short <= PV_SETTING_SLOPE_W_MIN
    ):
        return SUN_SETTING, (
            f"produzione in calo di {-slope:.0f}W/min da {span:.0f}min, "
            f"il sole sta calando"
        )
    return SUN_DAY, (
        f"produzione a {slope:+.0f}W/min su {span:.0f}min, non sta calando"
    )


def evaluate_smart_charge(
    now: datetime,
    signals: PowerSignals,
    forecast: ForecastSignals,
    need: ChargeNeed,
    cfg: SmartConfig,
    state: SmartState,
    refresh_rate: int,
    charging_allowed: bool = True,
    critical_target_amps: Optional[float] = None,
    force_critical: bool = False,
    force_reason: str = "",
) -> SmartDecision:
    """Run one control cycle and return the current to command.

    `critical_target_amps`, when supplied, is the grid-hybrid target computed by the
    existing deadline logic. It replaces the surplus-derived target while urgency is
    CRITICAL, but still flows through this state machine so that session and stop
    bookkeeping stay consistent across the whole day.

    `force_critical` lets the caller assert CRITICAL regardless of the slack the
    planner would compute on its own. It exists so the pre-existing deadline and
    AUTO phase-1 rules keep exactly the behaviour they had before this planner was
    introduced: the planner may make charging start *earlier*, never later.
    """
    state.roll_day(now)
    # The battery reading is evidence of capability only when the battery was not
    # competing for power: either the car is off, or we are exporting anyway.
    grid_now = signals.resolved_grid_w()
    trustworthy = signals.ev_w <= 50.0 or grid_now < -50.0
    state.note_battery(
        now, max(0.0, signals.battery_w), cfg.capability_window_min, trustworthy
    )
    state.note_pv(now, max(0.0, signals.pv_w))

    urgency, slack, urgency_reason = classify_urgency(need, forecast, cfg, signals.voltage)
    if force_critical:
        urgency = URGENCY_CRITICAL
        urgency_reason = force_reason or "forzato dalla logica deadline esistente"
    min_a = cfg.charger_min_current
    max_a = cfg.charger_max_current
    volts = max(signals.voltage, 1.0)
    min_watts = min_a * volts

    reserve_w, _reserve_start_w, share, projected_soc, reserve_reason = battery_reserve(
        signals, forecast, need, cfg, state, urgency, min_watts
    )
    raw_headroom_w, grid_w = compute_headroom_w(signals, reserve_w, cfg)
    headroom_w = state.update_headroom(raw_headroom_w, refresh_rate, cfg.headroom_ema_s)

    # Production, smoothed: the instantaneous reading swings by a kilowatt with
    # every cloud edge, and no stop decision should ride on that. `sun_phase` then
    # says whether a low reading means the day is over or merely overcast.
    pv_w = forecast.pv_avg_w if forecast.pv_avg_w > 0 else signals.pv_w
    phase, sun_reason = sun_phase(state, forecast, cfg, pv_w)
    pv_slope_w_min, _pv_span_min = state.pv_slope()

    def decide(amps: float, reason: str, state_name: str, dwell_left: int = 0) -> SmartDecision:
        return SmartDecision(
            amps=amps,
            reason=reason,
            state_name=state_name,
            urgency=urgency,
            headroom_w=headroom_w,
            reserve_w=reserve_w,
            share_factor=share,
            capability_w=state.observed_capability_w(),
            trend=forecast.trend,
            grid_w=grid_w,
            projected_batt_soc=projected_soc,
            stops_today=state.stops_today,
            dwell_left_s=dwell_left,
            slack=slack,
            sun_phase=phase,
            sun_reason=sun_reason,
            pv_slope_w_min=pv_slope_w_min,
        )

    def stop(reason: str) -> SmartDecision:
        if state.charging:
            state.stops_today += 1
            state.last_stop = now
        state.charging = False
        state.session_start = None
        state.start_dwell_s = 0
        state.stop_dwell_s = 0
        state.last_amps = 0.0
        return decide(0.0, reason, STATE_STOPPING)

    if not charging_allowed:
        return stop("Ricarica disabilitata dall utente")

    # CRITICAL bypasses the anti-cycling guards on its own, whether or not the
    # caller supplied a grid-hybrid target: a missed deadline is worse than a cycle.
    is_critical = urgency == URGENCY_CRITICAL

    # -- The rule ------------------------------------------------------------
    #
    #   start   there is enough surplus to run the charger at its minimum
    #   hold    keep at least that minimum for as long as the panels produce
    #           anything at all, even when the house is eating the whole surplus
    #   stop    the day is over -- production below the threshold *and* the sun on
    #           its way down -- or the home battery has fallen below its floor
    #
    # Starting on surplus and stopping on production is deliberately asymmetric,
    # and the asymmetry is the whole design. Surplus is what tells you charging is
    # worth beginning; production is what tells you the day is over. Using surplus
    # for both would end a session every time the air conditioning came on -- and
    # with this car a handful of interruptions is enough that it stops accepting a
    # restart until someone goes and opens it.
    #
    # The gap between the two conditions is enormous on purpose: once open, a
    # session normally lasts until sunset.
    battery_ok = signals.battery_soc >= cfg.solar_min_soc
    producing = pv_w >= cfg.solar_stop_pv_w
    day_over = phase != SUN_DAY

    # The start test is on the surplus itself -- PV minus house, smoothed -- and
    # not on what is left after the battery reserve. The reserve exists to decide
    # how far *above* its minimum the car may climb; using it to decide whether the
    # car may run at all sets a bar the surplus rarely clears, and the car simply
    # never starts. Once running, the home battery covers the dips, bounded by its
    # own SOC floor, which is the trade this design makes on purpose.
    start_headroom_w = headroom_w + reserve_w

    surplus_amps = headroom_w / volts
    if state.charging or is_critical:
        surplus_amps = max(surplus_amps, min_a)
    elif start_headroom_w >= min_watts:
        surplus_amps = max(surplus_amps, min_a)
    target_amps = surplus_amps
    if is_critical and critical_target_amps is not None:
        target_amps = max(surplus_amps, critical_target_amps)

    # -- Not charging: decide whether to start -------------------------------
    if not state.charging:
        blockers = []

        if not is_critical:
            if state.stops_today >= cfg.max_stops_per_day:
                blockers.append(
                    f"budget stop esaurito ({state.stops_today}/{cfg.max_stops_per_day} oggi)"
                )
            if state.last_stop is not None:
                elapsed = (now - state.last_stop).total_seconds() / 60.0
                if elapsed < cfg.min_off_min:
                    blockers.append(
                        f"pausa minima {cfg.min_off_min}min non trascorsa ({elapsed:.0f}min)"
                    )
            if not battery_ok:
                blockers.append(
                    f"batteria di casa al {signals.battery_soc:.0f}%, sotto il "
                    f"{cfg.solar_min_soc:.0f}% richiesto"
                )
            # Opening a session the sunset is about to close is a contactor cycle
            # spent on a few hundred watt-hours. The same test that keeps a session
            # alive through a cloud keeps one from starting into the evening.
            if day_over:
                blockers.append(sun_reason)

        if blockers:
            state.start_dwell_s = 0
            return decide(0.0, "Attesa: " + "; ".join(blockers), STATE_BLOCKED)

        # Enough surplus to actually run the charger at its minimum?
        if not is_critical and start_headroom_w < min_watts:
            state.start_dwell_s = max(0.0, state.start_dwell_s - refresh_rate * DWELL_DECAY)
            progress = ""
            if state.start_dwell_s > 0:
                progress = (f" Conferma al {state.start_dwell_s / max(cfg.start_dwell_s, 1):.0%}, "
                            f"non azzerata.")
            return decide(
                0.0,
                f"Attesa: {start_headroom_w:.0f}W disponibili per l'auto, per partire "
                f"a {min_a:.0f}A ne servono {min_watts:.0f}W.{progress}",
                STATE_IDLE,
            )

        dwell_target = 0 if is_critical else cfg.start_dwell_s
        state.start_dwell_s += refresh_rate
        if state.start_dwell_s < dwell_target:
            left = dwell_target - state.start_dwell_s
            return decide(
                # Zero, not the charger minimum. Commanding 6 A here would close the
                # contactor for real, and a dip before the dwell expired would then
                # open it again: the confirmation timer would be manufacturing
                # exactly the start/stop cycle it exists to prevent.
                0.0,
                f"Pronto a partire ({target_amps:.1f}A disponibili), conferma tra {left}s",
                STATE_WAITING,
                dwell_left=left,
            )

        state.charging = True
        state.session_start = now
        state.start_dwell_s = 0
        state.stop_dwell_s = 0
        # Open at what the ramp basis supports right now, floored at the minimum.
        # Using the start basis here (which only holds the battery to its floor)
        # opened the session several amps above the sustainable level, and the next
        # few cycles spent themselves ramping back down.
        opening_amps = target_amps
        if not is_critical:
            opening_amps = max(min_a, min(target_amps, headroom_w / volts))
        amps = float(int(_clamp(opening_amps, min_a, max_a)))
        state.last_amps = amps
        return decide(
            amps,
            f"Avvio a {amps:.0f}A: {start_headroom_w:.0f}W disponibili, batteria di casa al "
            f"{signals.battery_soc:.0f}%. {reserve_reason}",
            STATE_CHARGING,
        )

    # -- Charging: hold the minimum while the sun lasts ----------------------
    #
    # Two conditions end a session, and nothing else:
    #
    #   * the panels stop producing (`solar_stop_pv_w`)
    #   * the home battery drops below `solar_min_soc`
    #
    # Everything in between -- a cloud, the air conditioner starting, the house
    # briefly eating the whole surplus -- is covered by holding the charger
    # minimum and letting the home battery make up the difference. That is the
    # deliberate trade: a few hundred watt-hours through the battery instead of an
    # interruption this car may not come back from.
    session_min = 0.0
    if state.session_start is not None:
        session_min = (now - state.session_start).total_seconds() / 60.0

    if is_critical:
        state.stop_dwell_s = 0
        amps = _apply_ramp(_clamp(target_amps, min_a, max_a), state.last_amps,
                           cfg, min_a, max_a)
        state.last_amps = amps
        return decide(amps, f"Carica a {amps:.0f}A: {urgency_reason}", STATE_CHARGING)

    if not battery_ok:
        return stop(
            f"Stop dopo {session_min:.0f}min: batteria di casa scesa al "
            f"{signals.battery_soc:.0f}%, sotto il {cfg.solar_min_soc:.0f}% "
            f"che le ho riservato"
        )

    if not producing and day_over:
        state.stop_dwell_s += refresh_rate
        if state.stop_dwell_s >= cfg.stop_dwell_s:
            return stop(
                f"Stop dopo {session_min:.0f}min: giornata finita, {pv_w:.0f}W "
                f"sotto {cfg.solar_stop_pv_w:.0f}W e {sun_reason}"
            )
        left = cfg.stop_dwell_s - state.stop_dwell_s
        amps = _apply_ramp(min_a, state.last_amps, cfg, min_a, max_a)
        state.last_amps = amps
        return decide(
            amps,
            f"Produzione a {pv_w:.0f}W, {sun_reason}: tengo {amps:.0f}A ancora "
            f"{left}s prima di staccare",
            STATE_STOPPING,
            dwell_left=left,
        )

    # Below the threshold, but the sun is still up: this is weather, and weather is
    # what the home battery is here for. Riding it out at the charger minimum costs
    # a few hundred watt-hours through the battery; stopping costs a contactor
    # cycle and, on this car, possibly the rest of the day. The battery floor is
    # what bounds the wait, and it is checked above.
    if not producing:
        state.stop_dwell_s = 0
        amps = _apply_ramp(min_a, state.last_amps, cfg, min_a, max_a)
        state.last_amps = amps
        return decide(
            amps,
            f"Produzione bassa ({pv_w:.0f}W) ma {sun_reason}: tengo {amps:.0f}A e "
            f"la differenza la copre la batteria di casa ({signals.battery_soc:.0f}%)",
            STATE_HOLD,
        )

    # The panels are producing and the battery is above its floor: hold at least
    # the minimum, and climb above it only as far as the surplus allows.
    state.stop_dwell_s = 0
    amps = _apply_ramp(_clamp(target_amps, min_a, max_a), state.last_amps,
                       cfg, min_a, max_a)
    state.last_amps = amps

    if headroom_w < min_watts:
        return decide(
            amps,
            f"Il fotovoltaico produce ancora ({pv_w:.0f}W) ma la casa se lo sta "
            f"prendendo: tengo {amps:.0f}A, la differenza la copre la batteria "
            f"({signals.battery_soc:.0f}%)",
            STATE_HOLD,
        )
    return decide(
        amps,
        f"Carica a {amps:.0f}A dal sole ({pv_w:.0f}W di produzione). {reserve_reason}",
        STATE_CHARGING,
    )


def _apply_ramp(
    target: float, previous: float, cfg: SmartConfig, min_a: float, max_a: float
) -> float:
    """Rate-limit the setpoint in both directions and floor it to whole amps.

    Limiting the *rise* matters as much as limiting the fall: jumping straight to
    the computed target overshoots the real surplus, which pulls from the battery or
    the grid, which forces a correction down on the next cycle. That oscillation is
    what a slow ramp removes.
    """
    if previous >= min_a:
        target = min(target, previous + cfg.ramp_up_a)
        target = max(target, previous - cfg.ramp_down_a)
    return float(int(_clamp(target, min_a, max_a)))
