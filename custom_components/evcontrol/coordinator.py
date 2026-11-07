"""Data coordinator for EV Control."""
import logging
from collections import deque
from datetime import timedelta, datetime
from typing import Any, Dict, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .helpers import (
    calculate_surplus,
    get_available_battery_power,
    calculate_available_current,
    apply_eco_timer,
    apply_auto_timer,
    calculate_charge_time_hours,
    default_grid_margin_w,
    get_next_target_time,
    grid_import_cap_amps,
    resolve_target_soc,
    calculate_excess_after_battery,
)
from .status import build_debug, build_status, legacy_status
from .smart_charging import (
    ChargeNeed,
    ForecastSignals,
    PowerSignals,
    SmartConfig,
    SmartState,
    evaluate_smart_charge,
)

_LOGGER = logging.getLogger(__name__)

DOMAIN = "evcontrol"

# Weekday keys of the target schedule, in `datetime.weekday()` order. The schedule
# itself lives in the config entry options as `target_schedule_<day>`.
SCHEDULE_DAYS = ["monday", "tuesday", "wednesday", "thursday",
                 "friday", "saturday", "sunday"]

# Headroom kept below the contract limit when charging from the grid, so a sudden
# household load does not trip the meter. Previously the Normal-mode battery
# reservation setting was passed in here, which meant one slider silently drove two
# unrelated things.
# Head left unused on the supply so that a load starting up -- a water heater,
# an oven -- lands inside the contract instead of outside it. The car is the
# only load here that can be throttled, so the whole reserve comes out of its
# share. Configurable as `grid_safety_margin_w`; the default covers a typical
# resistive appliance, because a sudden start is exactly the case the reserve
# exists for and the control loop cannot react before it happens.
GRID_SAFETY_MARGIN_W = 1500.0

# How often the current setpoint is re-sent to the wallbox even when the computed
# value has not changed. Writing only on change leaves no way back from a setpoint
# that was lost or overwritten outside this integration: the shadow copy and the
# charger disagree, and the deadband keeps them disagreeing forever. Re-asserting
# costs one service call every two minutes.
SETPOINT_REASSERT_SECONDS = 120.0

# Gap between the setpoint we commanded and the value the number entity reports
# back that counts as "something else changed it". The entity is a float and some
# wallboxes round to their own step, so a strict comparison would rewrite forever.
SETPOINT_READBACK_TOLERANCE_A = 0.5

# Power above which an independent measurement is taken to mean the car really is
# drawing current, rather than the wallbox electronics idling or two meters
# disagreeing about the same installation.
UNWANTED_CHARGE_FLOOR_W = 700.0

# Consecutive cycles of measured draw against a 0A command before it is treated as
# a real runaway. At the default 20s refresh this is one minute: long enough for the
# wallbox to obey a ramp-down, short enough to catch a stuck contactor quickly.
UNWANTED_CHARGE_STRIKES = 3

# Minimum gap between stop-button presses, so a charger that refuses to stop is not
# hammered once per cycle.
STOP_PRESS_COOLDOWN_SECONDS = 300.0


class EVControlCoordinator(DataUpdateCoordinator):
    """Data coordinator for EV Control."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.config_entry = config_entry
        self.config = {**config_entry.data, **config_entry.options}

        # Persistent storage for mode
        self.store = Store(hass, 1, f"evcontrol_state_{config_entry.entry_id}")

        # ECO mode timers (10 minutes = 600 seconds)
        self.eco_timer_seconds = 0  # Current timer value
        self.eco_last_state = "stopped"  # Start idle until we actually see solar charging
        self.eco_timer_active = False  # Is timer running?

        # AUTO mode timers (10 minutes = 600 seconds)
        self.auto_timer_seconds = 0  # Current timer value
        self.auto_last_state = "stopped"  # Start idle until we actually see solar charging
        self.auto_timer_active = False  # Is timer running?

        # AUTO mode state
        self.ev_soc_previous = 0.0  # Previous EV SOC for capacity estimation
        self.ev_charging_speed_percent_per_hour = 0.0  # Measured charging speed from real data
        self.ev_battery_capacity_kwh = self.config.get("ev_battery_capacity_kwh", 42.0)  # Default 42 kWh (BYD Dolphin Surf)
        self.auto_last_charge_start = None  # Track when AUTO mode started charging
        self.smart_solar_protection = False  # Smart protection: hold min charge when PV active but house spike

        # Smart planner (see smart_charging.py). Holds the session state machine,
        # the stop budget and the rolling windows that make the battery reservation
        # adaptive instead of a fixed number of watts.
        self.smart_state = SmartState()
        # Rolling house-load average, used to turn a Solcast production forecast
        # into a *surplus* forecast. 30 samples at the default 20s refresh = 10 min.
        self.house_samples = deque(maxlen=30)
        # Rolling PV average. Used to calibrate the Solcast model against the real
        # array; must be averaged, or a cloud overhead would scale the whole
        # forward forecast down by the depth of a transient.
        self.pv_samples = deque(maxlen=30)
        # Last value actually sent to the wallbox, so the write deadband compares
        # against what the charger has rather than what we last computed.
        self.last_written_current: Optional[float] = None
        # When that write happened. Writing only on change assumes the wallbox keeps
        # the setpoint forever; it does not. An OCPP reconnect, a charger reboot, the
        # vendor app or a reload of the wallbox integration can put the setpoint back
        # to its own default while our shadow copy still says 0A -- and then nothing
        # ever writes again. The setpoint is re-asserted periodically, not just on
        # change.
        self.last_write_time: Optional[datetime] = None
        # Consecutive cycles in which independent measurements said the car was
        # drawing power we did not ask for. Debounced, because one such cycle is
        # normal: meters lag the setpoint by seconds.
        self.unwanted_charge_strikes = 0
        self.last_stop_press: Optional[datetime] = None
        self.unwanted_charge_reason: Optional[str] = None
        
        # Grid configuration from contract
        self.grid_max_current_amps = self.config.get("grid_max_current_amps", 32)  # Contract max current (A)
        self.grid_voltage = self.config.get("grid_voltage", 230)  # Grid voltage (V)
        
        # Build target schedule from config
        self.target_schedule = {}
        self.target_soc_schedule = {}
        self._rebuild_target_schedule()

        # Initialize state
        self.data = {
            "charging_enabled": True,
            "mode": "eco",
            "base_current": self.config.get("charger_min_current", 6),
            "grid_max_current_amps": self.grid_max_current_amps,
            "grid_voltage": self.grid_voltage,
            "pv_power": 0,
            "house_consumption": 0,
            "ev_power": 0,
            "battery_soc": 0,
            "battery_power": 0,
            "surplus": 0,
            "voltage": 230,
            "available_current": 0,
            "debug_message": "",
            "battery_reservation_power": self.config.get("battery_reservation_power", 500.0),
            "ev_soc": 0,  # EV SOC from MQTT
            "ev_battery_capacity_kwh": self.ev_battery_capacity_kwh,
            "target_schedule": dict(self.target_schedule),
            "target_soc_schedule": dict(self.target_soc_schedule),
        }

        update_interval = timedelta(seconds=self.config.get("refresh_rate", 20))
        super().__init__(
            hass,
            _LOGGER,
            name="EV Control",
            update_interval=update_interval,
        )
        
    async def async_config_entry_first_refresh(self) -> None:
        """Load saved state from storage before first refresh."""
        # DataUpdateCoordinator.__init__ resets self.data = None after our __init__ sets it.
        # Re-initialize as an empty dict so the key assignments below don't crash with TypeError.
        if self.data is None:
            self.data = {}
        try:
            saved_data = await self.store.async_load() or {}
            
            # Load mode from storage - MUST be done BEFORE parent refresh
            if "mode" in saved_data:
                mode = saved_data["mode"]
                if mode in ["eco", "normal", "power", "auto", "auto-eco"]:
                    self.data["mode"] = mode
                    _LOGGER.info(f"Loaded persisted mode: {mode}")

            # Load charging state so restart resumes the last run state
            if "charging_enabled" in saved_data:
                self.data["charging_enabled"] = bool(saved_data["charging_enabled"])
                _LOGGER.info(f"Loaded charging_enabled from storage: {self.data['charging_enabled']}")
            
            # Load grid_power_limit from storage
            if "grid_power_limit" in saved_data:
                self.grid_max_current_amps = (saved_data["grid_power_limit"] * 1000) / self.grid_voltage
                self.data["grid_max_current_amps"] = self.grid_max_current_amps
                _LOGGER.info(f"Loaded grid_power_limit from storage: {saved_data['grid_power_limit']}kW ({self.grid_max_current_amps}A)")
            
            # Load base_current from storage
            if "base_current" in saved_data:
                self.data["base_current"] = saved_data["base_current"]
                _LOGGER.info(f"Loaded base_current from storage: {saved_data['base_current']}A")
            
            # Load battery_reservation_power from storage
            if "battery_reservation_power" in saved_data:
                self.data["battery_reservation_power"] = saved_data["battery_reservation_power"]
                _LOGGER.info(f"Loaded battery_reservation_power from storage: {saved_data['battery_reservation_power']}W")

            # Load smart_solar_protection from storage
            if "smart_solar_protection" in saved_data:
                self.smart_solar_protection = bool(saved_data["smart_solar_protection"])
                _LOGGER.info(f"Loaded smart_solar_protection from storage: {self.smart_solar_protection}")

            # Load the live-tunable planner values
            for key in self.SMART_RUNTIME_KEYS:
                if key in saved_data:
                    self.data[key] = saved_data[key]
                    _LOGGER.info(f"Loaded {key} from storage: {saved_data[key]}")
            
        except Exception as e:
            _LOGGER.warning(f"Could not load saved state from storage: {e}")
        
        # Call parent refresh AFTER loading saved state
        await super().async_config_entry_first_refresh()

    async def _async_update_data(self) -> Dict[str, Any]:
        """Fetch data from Home Assistant and decide the charging current.

        The grid exchange is not read from a sensor: it is derived from the energy
        balance (loads + battery - PV), which is exact and has an unambiguous sign.
        A dedicated grid meter used to be configurable here, but its sign
        convention varies by inverter and getting it backwards would invert the
        whole control loop.
        """
        try:
            # Get voltage first (needed for EV power calculation)
            voltage = self._get_sensor_value(
                self.config.get("voltage_entity"), 230
            )
            
            # Calculate EV power from charger current FIRST (not from sensor)
            # This is real-time: charger_current × voltage
            # If charger is not charging, EV power = 0
            ev_power = self._calculate_ev_power_from_charger(voltage)
            _LOGGER.debug(f"EV Power (from charger current): {ev_power}W")
            
            # Get EV-specific sensors for AUTO mode
            ev_soc = self._get_sensor_value(
                self.config.get("ev_soc_entity"), None
            )
            
            # Get current mode early to decide if we should validate auto power
            # (This is duplicated later, but needed here for power validation)
            current_mode = self.data.get("mode", "eco") if self.data else "eco"
            
            # For AUTO/AUTO-ECO modes: Use validated EV power that accounts for transmission losses
            # The auto power entity reports what the EV actually receives (more accurate for charging calc)
            # vs charger power which includes transmission losses
            if current_mode in ["auto", "auto-eco"] and ev_power > 0:
                validated_ev_power = self._get_validated_ev_power(ev_power)
                _LOGGER.debug(f"AUTO mode: Using validated EV power: {validated_ev_power:.1f}W (was {ev_power}W)")
                ev_power = validated_ev_power
            
            # Get entity states
            pv_power = self._get_sensor_value(
                self.config.get("pv_power_entity"), 0
            )
            house_consumption_raw = self._get_sensor_value(
                self.config.get("consumption_entity"), 0
            )
            battery_soc = self._get_sensor_value(
                self.config.get("battery_soc_entity"), 0
            )

            # Calculate actual house consumption (subtract EV if included in sensor)
            # IMPORTANT: The EV power is calculated in real-time from charger_current × voltage
            # So if consumption sensor includes EV, we need to subtract this calculated EV power
            if self.config.get("consumption_includes_ev", False):
                # Sensor includes EV power, subtract it to get real house consumption
                house_consumption = max(0, house_consumption_raw - ev_power)
                _LOGGER.debug(f"House Consumption: {house_consumption_raw}W (sensor) - {ev_power}W (EV) = {house_consumption}W")
            else:
                # Sensor is house-only, use as-is
                house_consumption = house_consumption_raw
                _LOGGER.debug(f"House Consumption: {house_consumption}W (sensor is house-only)")

            # Prefer measured EV power over the charger setpoint when a total-load
            # sensor is available (see _measured_ev_power for why this matters).
            ev_power = self._measured_ev_power(house_consumption, ev_power)

            # Rolling house average feeds the Solcast surplus projection.
            self.house_samples.append(house_consumption)
            house_avg_w = sum(self.house_samples) / len(self.house_samples)
            self.pv_samples.append(pv_power)
            pv_avg_w = sum(self.pv_samples) / len(self.pv_samples)

            # Calculate surplus - always use PV calculation as primary
            # Surplus = PV - House_consumption
            # Do NOT subtract actual EV power - that's used separately for ampere calculation
            surplus = calculate_surplus(
                pv_power,
                house_consumption,  # House only, NOT including EV actual consumption
                ev_power,
                self.config.get("consumption_includes_ev", False),
            )
            

            # Get battery available power (for use in modes)
            battery_power = get_available_battery_power(
                battery_soc,
                self.config.get("battery_min_soc", 20),
                self.config.get("battery_max_drain_power", 2500),
            )

            # Read actual battery charge power if sensor is configured.
            # Sign convention: positive = battery charging, negative = battery discharging.
            # Used to give EV the true available surplus instead of an overestimated reservation.
            actual_battery_charge_w = None
            # Signed value (+ charging / - discharging). The smart planner needs the
            # sign: a discharging battery means the house is already short, which is
            # the opposite of surplus.
            raw_battery_power_signed = 0.0
            battery_power_entity = self.config.get("battery_power_entity")
            if battery_power_entity:
                raw_battery_power = self._get_sensor_value(battery_power_entity, None)
                if raw_battery_power is not None:
                    raw_battery_power_signed = raw_battery_power
                    # Only use positive values (battery charging); negative = discharging, irrelevant here
                    actual_battery_charge_w = max(0.0, raw_battery_power)
                    _LOGGER.debug(f"Battery actual charge power: {actual_battery_charge_w:.0f}W (raw={raw_battery_power:.0f}W)")

            # UPDATE AUTO MODE: Track charging speed from real EV SOC data
            if ev_soc is not None and self.ev_soc_previous is not None:
                soc_delta = ev_soc - self.ev_soc_previous
                
                # SAFETY: Validate SOC delta is realistic for 20-second cycle
                # Max realistic delta = 60%/h / 180 cycles/hour = 0.33% per cycle
                # Allow up to 1% as safety margin (accounts for data collection delays)
                # If delta > 1%, it's likely MQTT reconnection after long gap
                max_delta_per_cycle = 1.0  # %
                if abs(soc_delta) > max_delta_per_cycle:
                    _LOGGER.warning(f"SOC delta {soc_delta:.2f}% in 20s cycle is unrealistic (likely MQTT gap after reconnection), resetting speed tracker and SOC history")
                    # Reset the speed tracker to prevent stale extreme values
                    self.ev_charging_speed_percent_per_hour = 0.0
                    # Reset SOC history to current value so next cycle calculates correctly
                    self.ev_soc_previous = ev_soc
                    # Skip speed calculation this cycle
                elif ev_power is not None and ev_power > 0 and self.ev_battery_capacity_kwh > 0:
                    # EV Power is in Watts, Battery Capacity in kWh
                    charging_speed_percent_per_hour = (ev_power / 1000.0 / self.ev_battery_capacity_kwh) * 100
                    
                    # SAFETY: Validate charging speed is realistic
                    # For 45kWh battery, max speed is ~100%/h at 32A × 230V = 7.36kW
                    # For typical home chargers (16A) → max ~30%/h
                    # Allow up to 60%/h as safety margin for fast chargers
                    max_realistic_speed = 60.0  # %/h
                    if charging_speed_percent_per_hour > max_realistic_speed:
                        _LOGGER.warning(f"EV charging speed {charging_speed_percent_per_hour:.1f}%/h is unrealistic (max {max_realistic_speed}%/h for {self.ev_battery_capacity_kwh}kWh), ignoring to prevent overshoot (EV Power={ev_power}W, likely stale/corrupted data)")
                        # Don't update speed, keep previous value or 0
                        if self.ev_charging_speed_percent_per_hour == 0:
                            pass  # Stay at 0 until we get valid data
                    else:
                        # Update with weighted average (75% new, 25% old) to smooth out noise
                        self.ev_charging_speed_percent_per_hour = (
                            0.75 * charging_speed_percent_per_hour + 
                            0.25 * self.ev_charging_speed_percent_per_hour
                        )
                        _LOGGER.debug(f"EV charging speed: {charging_speed_percent_per_hour:.2f}%/h (EV Power={ev_power}W, Capacity={self.ev_battery_capacity_kwh}kWh, averaged: {self.ev_charging_speed_percent_per_hour:.2f}%/h)")
                elif soc_delta > 0:
                    # Fallback: use SOC delta if available (less accurate but works)
                    refresh_rate = self.config.get("refresh_rate", 20)
                    hours_elapsed = refresh_rate / 3600.0
                    charging_speed_percent_per_hour = soc_delta / hours_elapsed
                    
                    self.ev_charging_speed_percent_per_hour = (
                        0.75 * charging_speed_percent_per_hour + 
                        0.25 * self.ev_charging_speed_percent_per_hour
                    )
                    _LOGGER.debug(f"EV charging speed (SOC delta): {charging_speed_percent_per_hour:.2f}%/h (averaged: {self.ev_charging_speed_percent_per_hour:.2f}%/h)")
                else:
                    # No valid speed data this cycle (ev_power = 0, charger paused)
                    # DO NOT decay the speed value!
                    # When charger resumes, we need accurate speed data from before the pause
                    # Decay only happens via explicit SOC delta detection (corrupt data)
                    _LOGGER.debug(f"No speed data this cycle (ev_power={ev_power}W, soc_delta={soc_delta:.2f}%), keeping previous speed {self.ev_charging_speed_percent_per_hour:.1f}%/h")
                
                self.ev_soc_previous = ev_soc

            # Get current state values for mode, base_current, grid_power_limit, and charging_enabled
            # These can be changed at runtime and are stored in self.data, not self.config
            # Note: current_mode is already fetched earlier for power validation in AUTO mode
            current_base_current = self.data.get("base_current", self.config.get("charger_min_current", 6)) if self.data else self.config.get("charger_min_current", 6)
            current_charging_enabled = self.data.get("charging_enabled", True) if self.data else True
            current_battery_reservation = self.data.get("battery_reservation_power", self.config.get("battery_reservation_power", 500.0)) if self.data else self.config.get("battery_reservation_power", 500.0)
            
            # Calculate grid power limit dynamically from contract current
            # grid_max_power_kw = (current_A × voltage_V) / 1000
            grid_max_power_kw = (self.grid_max_current_amps * self.grid_voltage) / 1000.0
            # Default scaled to the supply: a flat reserve that suits 6 kW
            # stops a 3 kW contract charging at all. See default_grid_margin_w.
            _configured_margin = self.config.get("grid_safety_margin_w")
            grid_margin_w = (
                float(_configured_margin)
                if _configured_margin is not None
                else default_grid_margin_w(grid_max_power_kw * 1000.0)
            )

            # Next scheduled target. Read in every mode: only AUTO/AUTO-ECO act on
            # it, but the diagnostics have to answer "when does this get critical?"
            # precisely when you are sitting in NORMAL or POWER wondering about it.
            # `ev_soc` is passed so that a deadline already satisfied -- 80% asked
            # for, 80% in the car -- does not claim the post-deadline grace window
            # and pull grid power for a charge that is already finished.
            schedule_seconds, target_time_str, auto_target_soc = get_next_target_time(
                self.target_schedule,
                datetime.now(),
                soc_schedule=self.target_soc_schedule,
                ev_soc=ev_soc,
            )
            seconds_until_auto_target = (
                schedule_seconds if current_mode in ["auto", "auto-eco"] else None
            )
            if target_time_str:
                _LOGGER.debug(
                    f"Next target {target_time_str} at {auto_target_soc:.0f}% "
                    f"in {schedule_seconds}s "
                    f"(acted on: {current_mode in ['auto', 'auto-eco']})"
                )

            # The smart planner owns the solar-first modes when enabled. NORMAL and
            # POWER are untouched: they are explicit user overrides, not surplus
            # tracking, so there is nothing for the planner to optimise there.
            smart_cfg = SmartConfig.from_config({**self.config, **self._smart_overrides()})
            use_smart = smart_cfg.enabled and current_mode in ("eco", "auto", "auto-eco")

            # Calculate available current based on mode
            available_current, debug_message = calculate_available_current(
                surplus,
                battery_power,
                grid_max_power_kw,
                battery_soc,
                self.config.get("battery_start_charge_soc", 70),
                voltage,
                current_base_current,
                self.config.get("charger_max_current", 32),
                current_mode,
                self.config.get("battery_min_soc", 20),
                house_consumption,  # Pass actual house consumption (EV subtracted) for grid calculation
                grid_margin_w,
                current_battery_reservation,
                ev_soc=ev_soc,
                ev_battery_capacity_kwh=self.ev_battery_capacity_kwh,
                seconds_until_auto_target=seconds_until_auto_target,
                automatic_charge_current=self.config.get("automatic_charge_current", 15),
                auto_phase1_soc_threshold=self.config.get("auto_phase1_soc_threshold", 50),
                ev_charging_speed_percent_per_hour=self.ev_charging_speed_percent_per_hour,
                auto_timer_active=self.auto_timer_active,
                auto_last_state=self.auto_last_state,
                actual_battery_charge_w=actual_battery_charge_w,
                target_soc=auto_target_soc,
                charger_floor_current=self.config.get("charger_min_current", 6),
            )

            # Apply ECO mode timer (10-minute hysteresis for cloud protection)
            if current_mode == "eco" and not use_smart:
                refresh_rate = self.config.get("refresh_rate", 20)
                available_current, self.eco_timer_seconds, self.eco_last_state, self.eco_timer_active, eco_debug = apply_eco_timer(
                    surplus,
                    battery_soc,
                    self.config.get("battery_start_charge_soc", 70),
                    self.config.get("charger_min_current", 6),
                    voltage,
                    self.eco_timer_seconds,
                    self.eco_last_state,
                    self.eco_timer_active,
                    refresh_rate,
                    smart_solar_protection=self.smart_solar_protection,
                    pv_power=pv_power,
                )
                debug_message = eco_debug
                _LOGGER.debug(f"ECO timer: {eco_debug}")

            # Apply AUTO/AUTO-ECO mode timer (10-minute hysteresis for cloud/fluctuation protection)
            # CRITICAL: Only apply timer if NOT in CRITICAL state
            # - If CRITICAL: calculate_available_current() returns grid ibrido, timer must NOT interfere
            # - If NON-CRITICAL: timer manages solar charging + oscillation protection
            # Criticality per the pre-existing deadline rule. Computed unconditionally
            # so the smart planner can honour it: the planner may bring charging
            # forward, but it must never delay a deadline the old logic considered
            # critical.
            legacy_is_critical = False
            legacy_critical_reason = ""
            if current_mode in ["auto", "auto-eco"] and seconds_until_auto_target is not None and ev_soc is not None:
                time_until_target_hours = seconds_until_auto_target / 3600.0
                time_needed_hours = calculate_charge_time_hours(
                    ev_soc, auto_target_soc, self.ev_battery_capacity_kwh,
                    self.config.get("automatic_charge_current", 15),
                    voltage,
                    battery_soc
                )
                # A deadline whose SOC is already reached is not a deadline. Without
                # this, the grace window would force grid charging on a car that has
                # nothing left to take.
                target_reached = ev_soc >= auto_target_soc - 0.5
                # Target passed (grace window): seconds_until_auto_target == 60 → always critical
                legacy_is_critical = (not target_reached) and (
                    (seconds_until_auto_target <= 60)
                    or (time_until_target_hours <= (time_needed_hours * 1.10))
                )
                if legacy_is_critical:
                    legacy_critical_reason = (
                        f"deadline: {time_until_target_hours:.1f}h disponibili, "
                        f"{time_needed_hours:.1f}h necessarie +10% "
                        f"per arrivare al {auto_target_soc:.0f}%"
                    )
            # AUTO phase 1 keeps its original meaning: below the SOC threshold the car
            # gets a guaranteed buffer regardless of sun. When the deadline is already
            # critical on its own, that is the stronger reason and it is kept: the
            # status text must not blame a far-away deadline for a buffer charge.
            phase1_soc = self.config.get("auto_phase1_soc_threshold", 50)
            auto_phase1_active = False
            if (
                current_mode == "auto"
                and ev_soc is not None
                and ev_soc < phase1_soc
                and not legacy_is_critical
            ):
                legacy_is_critical = True
                auto_phase1_active = True
                legacy_critical_reason = (
                    f"AUTO fase 1: SOC EV {ev_soc:.0f}% sotto la soglia {phase1_soc}%"
                )

            if not use_smart and current_mode in ["auto", "auto-eco"] and seconds_until_auto_target is not None and ev_soc is not None:
                is_critical = legacy_is_critical

                # Only apply timer if NON-CRITICAL (waiting for solar)
                if not is_critical:
                    refresh_rate = self.config.get("refresh_rate", 20)
                    
                    # Calculate excess_after_battery using the shared helper function
                    # (same logic as calculate_available_current() to stay in sync)
                    excess_after_battery_for_timer = calculate_excess_after_battery(
                        surplus,
                        battery_power,
                        battery_soc,
                        actual_battery_charge_w,
                    )
                    
                    # Apply timer for NON-CRITICAL AUTO/AUTO-ECO modes
                    # Timer manages: solar charging + ramp-up waits + hold_6a for oscillations
                    available_current, self.auto_timer_seconds, self.auto_last_state, self.auto_timer_active, auto_debug = apply_auto_timer(
                        surplus,
                        excess_after_battery_for_timer,
                        self.config.get("charger_min_current", 6),
                        voltage,
                        self.auto_timer_seconds,
                        self.auto_last_state,
                        self.auto_timer_active,
                        refresh_rate,
                        smart_solar_protection=self.smart_solar_protection,
                        pv_power=pv_power,
                    )
                    debug_message = auto_debug
                    _LOGGER.debug(f"AUTO timer (NON-CRITICAL): {auto_debug}")
                else:
                    # CRITICAL: Do NOT apply timer, use grid ibrido from calculate_available_current()
                    _LOGGER.debug(f"AUTO CRITICAL state (time {time_until_target_hours:.1f}h <= needed {time_needed_hours:.1f}h +10% margin): timer bypassed, using grid ibrido from calculate_available_current()")

            # ── Smart planner ────────────────────────────────────────────────
            # Replaces the start/stop decision for the solar-first modes. It keeps
            # its own session state machine, so the legacy eco/auto timers above are
            # skipped entirely rather than layered on top.
            smart_decision = None
            charge_status = None
            if use_smart:
                forecast = self._read_forecast(datetime.now(), house_avg_w, pv_avg_w)
                signals = PowerSignals(
                    pv_w=pv_power,
                    house_w=house_consumption,
                    ev_w=ev_power,
                    battery_w=raw_battery_power_signed,
                    battery_soc=battery_soc,
                    voltage=voltage,
                    # Deliberately derived from the energy balance rather than read
                    # from `grid_power_entity`: that sensor's sign convention is not
                    # established (see the docstring at the top of _async_update_data)
                    # and an inverted sign here would invert the whole control loop.
                    # The derivation is exact and unambiguous.
                    grid_w=None,
                )
                need = ChargeNeed(
                    ev_soc=ev_soc,
                    ev_capacity_kwh=self.ev_battery_capacity_kwh,
                    target_soc=auto_target_soc,
                    seconds_to_target=seconds_until_auto_target,
                    reference_current_a=self.config.get("automatic_charge_current", 15),
                )
                smart_decision = evaluate_smart_charge(
                    now=datetime.now(),
                    signals=signals,
                    forecast=forecast,
                    need=need,
                    cfg=smart_cfg,
                    state=self.smart_state,
                    refresh_rate=self.config.get("refresh_rate", 20),
                    charging_allowed=current_charging_enabled,
                    critical_target_amps=available_current if current_mode in ("auto", "auto-eco") else None,
                    force_critical=legacy_is_critical,
                    force_reason=legacy_critical_reason,
                )
                available_current = smart_decision.amps
                debug_message = smart_decision.reason
                charge_status = build_status(
                    now=datetime.now(),
                    decision=smart_decision,
                    signals=signals,
                    need=need,
                    cfg=smart_cfg,
                    state=self.smart_state,
                    mode=current_mode,
                    charging_enabled=current_charging_enabled,
                    target_label=target_time_str,
                    reference_current=self.config.get("automatic_charge_current", 15),
                    phase1_soc=phase1_soc if auto_phase1_active else None,
                )
                _LOGGER.debug(
                    f"Smart planner [{smart_decision.state_name}/{smart_decision.urgency}]: "
                    f"{smart_decision.amps:.0f}A, headroom={smart_decision.headroom_w:.0f}W, "
                    f"reserve={smart_decision.reserve_w:.0f}W, grid={smart_decision.grid_w:.0f}W, "
                    f"trend={smart_decision.trend}, sun={smart_decision.sun_phase} "
                    f"({smart_decision.pv_slope_w_min:+.0f}W/min) - {smart_decision.reason}"
                )

            # Don't charge if disabled or current is below minimum
            _LOGGER.debug(f"Before minimum check: available={available_current}A, min={self.config.get('charger_min_current', 6)}A, enabled={current_charging_enabled}, grid_available={grid_max_power_kw}kW")
            if not current_charging_enabled:
                available_current = 0
                debug_message = "Charging disabled by user"
            elif self.config.get("charger_status_entity"):
                # Monitor charger status for debugging, but don't artificially block charging
                status_state = self.hass.states.get(self.config.get("charger_status_entity"))
                if status_state is not None:
                    status = status_state.state.lower()
                    if status == "suspendedev":
                        # SuspendedEV: EV has paused, waiting for auto to be ready to resume
                        # Don't force 0A - just log and let auto decide when to resume
                        _LOGGER.info(f"Charger suspended by EV (SuspendedEV) - auto will resume when ready")
                    elif status == "idle":
                        # Keep debug info but don't block charging
                        _LOGGER.debug(f"Charger status is Idle, but continuing to send charge commands")
            elif available_current < self.config.get("charger_min_current", 6) and current_mode not in ["eco", "auto", "auto-eco"]:
                # For non-deadline modes (normal, power), set to 0 if below minimum
                # Deadline modes (auto, auto-eco) and eco mode bypass this check
                debug_message = (
                    f"Available current {available_current}A below minimum "
                    f"{self.config.get('charger_min_current', 6)}A"
                )
                available_current = 0
            _LOGGER.debug(f"After minimum check: available={available_current}A, message={debug_message}")

            # Apply ramp-down to avoid oscillations from stale sensor data
            # Max decrease: 2A per cycle to give sensors time to update
            #
            # Skipped under the smart planner: it rate-limits in both directions
            # itself, and this clamp would also stop a deliberate stop from ever
            # reaching 0A (it floors the value at last-2A every cycle).
            if not use_smart:
                last_available = self.data.get("available_current", 0) if self.data else 0
                ramp_step = 2.0  # Max 2A decrease per cycle

                if available_current < last_available:
                    # Decreasing: apply ramp to smooth out data lag
                    available_current = max(available_current, last_available - ramp_step)
                    _LOGGER.debug(f"Ramp-down applied: last={last_available}A, ramped={available_current}A (max -2A per cycle)")
                # If increasing, use immediately (no ramp-up needed)

            # ── Hard grid clamp ──────────────────────────────────────────────
            # Last word before the setpoint is written, and deliberately outside
            # every ramp: the ramps exist to be kind to the car, and the meter is
            # not interested. A 2 A/cycle descent took 40 seconds to come back
            # under contract when a 1.5 kW load started mid-session, peaking 13%
            # over. Downward, this applies at once.
            #
            # The budget is on *imported* power, not on the house total, so PV and
            # a discharging home battery both widen it: they are supply, and only
            # what the meter sees counts.
            grid_cap_a, import_headroom_w = grid_import_cap_amps(
                grid_limit_w=grid_max_power_kw * 1000.0,
                margin_w=grid_margin_w,
                house_w=house_consumption,
                pv_w=pv_power,
                battery_signed_w=raw_battery_power_signed or 0.0,
                voltage=voltage,
                charger_min_a=float(self.config.get("charger_min_current", 6)),
            )
            if available_current > grid_cap_a:
                _LOGGER.info(
                    "Grid clamp: %.0fA -> %.0fA (budget %.0fW = %.0fW limite "
                    "- %.0fW margine - %.0fW casa + %.0fW PV)",
                    available_current, grid_cap_a, max(0.0, import_headroom_w),
                    grid_max_power_kw * 1000.0, grid_margin_w,
                    house_consumption, pv_power,
                )
                charger_min_a = float(self.config.get("charger_min_current", 6))
                if grid_cap_a <= 0:
                    # Worth spelling out: a reserve too large for the supply
                    # produces exactly this, forever, and silently.
                    debug_message = (
                        f"{debug_message} | fermo per il limite di rete: restano "
                        f"{max(0.0, import_headroom_w):.0f}W, sotto i "
                        f"{charger_min_a * voltage:.0f}W del minimo colonnina "
                        f"({charger_min_a:.0f}A). Contratto {grid_max_power_kw:.1f}kW, "
                        f"margine {grid_margin_w:.0f}W, casa {house_consumption:.0f}W"
                    )
                else:
                    debug_message = (
                        f"{debug_message} | limite rete: {grid_cap_a:.0f}A "
                        f"({max(0.0, import_headroom_w):.0f}W disponibili)"
                    )
                available_current = grid_cap_a

            charge_debug = build_debug(
                now=datetime.now(),
                ev_soc=ev_soc,
                ev_capacity_kwh=self.ev_battery_capacity_kwh,
                ev_power_w=ev_power,
                seconds_to_target=schedule_seconds,
                target_label=target_time_str,
                reference_current_a=self.config.get("automatic_charge_current", 15),
                voltage=voltage,
                target_soc=auto_target_soc,
            )

            # Sunset detection, shown alongside the rest of the debug so the reason
            # a session did (or did not) end is visible without reading the log.
            if smart_decision is not None:
                charge_debug.attributes["sole"] = smart_decision.sun_phase
                charge_debug.attributes["sole_perche"] = smart_decision.sun_reason
                charge_debug.attributes["produzione_w_min"] = round(
                    smart_decision.pv_slope_w_min
                )

            status_message = debug_message
            auto_is_critical = False
            if charge_status is None:
                charge_status = legacy_status(
                    current_mode, debug_message, current_charging_enabled
                )
            auto_time_until_target_hours = None
            auto_time_needed_hours = None

            if current_mode in ["auto", "auto-eco"] and not use_smart:
                if seconds_until_auto_target is None:
                    status_message = f"{current_mode.upper()}: no target schedule configured"
                elif ev_soc is not None and self.ev_battery_capacity_kwh:
                    auto_time_until_target_hours = seconds_until_auto_target / 3600.0
                    auto_time_needed_hours = calculate_charge_time_hours(
                        ev_soc,
                        auto_target_soc,
                        self.ev_battery_capacity_kwh,
                        self.config.get("automatic_charge_current", 15),
                        voltage,
                        battery_soc,
                    )
                    auto_time_needed_with_margin = auto_time_needed_hours * 1.10
                    auto_is_critical = auto_time_until_target_hours <= auto_time_needed_with_margin
                    schedule_label = target_time_str or "unknown"
                    if auto_is_critical:
                        if available_current >= self.config.get("automatic_charge_current", 15):
                            status_message = (
                                f"{current_mode.upper()}: target {schedule_label} in {auto_time_until_target_hours:.1f}h, "
                                f"need {auto_time_needed_hours:.1f}h @15A (+10% margin {auto_time_needed_with_margin:.1f}h) -> CRITICAL, charging now at {available_current:.1f}A"
                            )
                        else:
                            status_message = (
                                f"{current_mode.upper()}: target {schedule_label} in {auto_time_until_target_hours:.1f}h, "
                                f"need {auto_time_needed_hours:.1f}h @15A (+10% margin {auto_time_needed_with_margin:.1f}h) -> CRITICAL, charging limited to {available_current:.1f}A"
                            )
                    else:
                        hours_until_start = max(0.0, auto_time_until_target_hours - auto_time_needed_with_margin)
                        if available_current >= self.config.get("charger_min_current", 6):
                            if self.auto_last_state == "smart_hold_6a":
                                status_message = (
                                    f"{current_mode.upper()}: target {schedule_label} in {auto_time_until_target_hours:.1f}h, "
                                    f"need {auto_time_needed_hours:.1f}h @15A (+10% margin {auto_time_needed_with_margin:.1f}h) -> NOT critical, "
                                    f"smart hold {available_current:.0f}A (PV {pv_power:.0f}W active, anti-cycle BYD)"
                                )
                            else:
                                status_message = (
                                    f"{current_mode.upper()}: target {schedule_label} in {auto_time_until_target_hours:.1f}h, "
                                    f"need {auto_time_needed_hours:.1f}h @15A (+10% margin {auto_time_needed_with_margin:.1f}h) -> NOT critical, charging at {available_current:.1f}A"
                                )
                        elif self.auto_timer_active and self.auto_last_state == "hold_6a":
                            status_message = (
                                f"{current_mode.upper()}: target {schedule_label} in {auto_time_until_target_hours:.1f}h, "
                                f"need {auto_time_needed_hours:.1f}h @15A (+10% margin {auto_time_needed_with_margin:.1f}h) -> NOT critical, timer holding 6A"
                            )
                        else:
                            status_message = (
                                f"{current_mode.upper()}: target {schedule_label} in {auto_time_until_target_hours:.1f}h, "
                                f"need {auto_time_needed_hours:.1f}h @15A (+10% margin {auto_time_needed_with_margin:.1f}h) -> NOT critical, will start in ~{hours_until_start:.1f}h"
                            )

            # Preserve the raw debug text as well, but expose the single structured status string
            debug_message = status_message

            # Independent check that the car draws what we asked for. Done before
            # the write, so a runaway can force the write below.
            unwanted_reason = self._detect_unwanted_charge(
                available_current, house_consumption, voltage
            )
            self.unwanted_charge_reason = unwanted_reason

            # Update charger current entity if configured.
            # Normally only on change, to avoid stressing the charger -- but the
            # setpoint is also re-asserted periodically and whenever the charger
            # disagrees with us, because "write once and assume it sticks" is how a
            # 0A command ends up charging at 16A.
            charger_current_entity = self.config.get("charger_current_entity")
            if charger_current_entity:
                # Compare against the last value actually written, not against the
                # last computed one: otherwise a series of sub-deadband steps would
                # drift the setpoint arbitrarily far without ever being sent.
                last_written = self.last_written_current
                changed = last_written != available_current

                # Rewriting the setpoint for a small wobble gives the wallbox nothing
                # useful and adds noise to the pilot signal. Crossing 0 always writes,
                # because that is a real start or stop.
                if changed and last_written is not None and use_smart:
                    crosses_zero = (available_current == 0) != (last_written == 0)
                    if not crosses_zero:
                        changed = abs(available_current - last_written) >= smart_cfg.write_deadband_a

                # Reasons to write even though the computed value did not change.
                force_reason = None

                # 1. The charger allows MORE current than we asked for, so something
                #    else moved the limit -- the vendor app, an OCPP reconnect, a
                #    reload of the wallbox integration. Without this check the
                #    deadband would stop us from ever correcting it.
                #
                #    Only this direction is acted on. A readback *below* the command
                #    is not the failure we are chasing, and on some wallboxes the
                #    entity echoes the measured current rather than the setpoint --
                #    a car drawing 15.2A of an offered 16A would then force a
                #    pointless write every single cycle. The periodic re-assert
                #    below catches a genuinely lost setpoint within two minutes
                #    anyway.
                readback = self._read_charger_setpoint(charger_current_entity)
                if (
                    readback is not None
                    and last_written is not None
                    and readback - last_written >= SETPOINT_READBACK_TOLERANCE_A
                ):
                    force_reason = (
                        f"charger allows {readback}A but we commanded {last_written}A "
                        f"(raised outside EV Control)"
                    )

                # 2. Measurements say the car is charging when it should not be.
                #    Re-asserting the setpoint is the first thing to try.
                if unwanted_reason:
                    force_reason = unwanted_reason

                # 3. Nothing visibly wrong, but the setpoint has not been refreshed
                #    in a while. Cheap insurance against a write that HA accepted
                #    and the charger silently dropped.
                if force_reason is None and not changed:
                    if self.last_write_time is None:
                        force_reason = "no setpoint written yet this session"
                    else:
                        age = (datetime.now() - self.last_write_time).total_seconds()
                        if age >= SETPOINT_REASSERT_SECONDS:
                            force_reason = f"periodic re-assert ({age:.0f}s since last write)"

                if changed or force_reason:
                    try:
                        await self.hass.services.async_call(
                            "number",
                            "set_value",
                            {
                                "entity_id": charger_current_entity,
                                "value": available_current,
                            },
                        )
                        self.last_written_current = available_current
                        self.last_write_time = datetime.now()
                        if unwanted_reason:
                            _LOGGER.warning(
                                f"Re-sent {available_current}A to {charger_current_entity}: "
                                f"{force_reason}"
                            )
                        elif force_reason:
                            _LOGGER.info(
                                f"Set {charger_current_entity} to {available_current}A "
                                f"({force_reason})"
                            )
                        else:
                            _LOGGER.info(f"Updated {charger_current_entity} to {available_current}A")
                    except Exception as e:
                        _LOGGER.error(f"Failed to update {charger_current_entity}: {e}")
                else:
                    _LOGGER.debug(
                        f"Charger current {available_current}A within deadband of last "
                        f"written {self.last_written_current}A, skipping update"
                    )

            # Re-asserting the setpoint does not always end a session the charger has
            # decided to keep: some wallboxes treat 0A as "no change" or clamp it to
            # their own minimum. If we wanted a stop and the draw is still there, use
            # the stop button -- the one command that definitely ends it.
            if unwanted_reason and available_current <= 0:
                await self._enforce_stop(unwanted_reason)

            # Create new data dict
            new_data = {
                "charging_enabled": current_charging_enabled,
                "mode": current_mode,
                "base_current": current_base_current,
                "grid_power_limit": grid_max_power_kw,
                "grid_safety_margin_w": grid_margin_w,
                "grid_cap_a": round(grid_cap_a, 1),
                "battery_reservation_power": current_battery_reservation,
                "pv_power": pv_power,
                "house_consumption": house_consumption,
                "ev_power": ev_power,
                "battery_soc": battery_soc,
                "battery_power": battery_power,
                "surplus": surplus,
                "voltage": voltage,
                "available_current": available_current,
                "ev_soc": ev_soc or 0,
                "ev_battery_capacity_kwh": self.ev_battery_capacity_kwh,
                "seconds_until_auto_target": seconds_until_auto_target,
                "debug_message": debug_message,
                "eco_timer_seconds": self.eco_timer_seconds,
                "eco_last_state": self.eco_last_state,
                "eco_timer_active": self.eco_timer_active,
                "auto_timer_seconds": self.auto_timer_seconds,
                "auto_last_state": self.auto_last_state,
                "auto_timer_active": self.auto_timer_active,
                "auto_target_time": target_time_str,
                # The weekly deadlines, so the card can show and edit them without
                # a second round trip to the config entry.
                "target_schedule": dict(self.target_schedule),
                # The SOC each deadline asks for, as a separate attribute: cards
                # written against 0.4.x parse every value of `target_schedule` as a
                # time, so widening that dict would blank their rows.
                "target_soc_schedule": dict(self.target_soc_schedule),
                "auto_target_soc": auto_target_soc,
                "auto_is_critical": auto_is_critical,
                "auto_time_until_target_hours": auto_time_until_target_hours,
                "auto_time_needed_hours": auto_time_needed_hours,
                "status_message": status_message,
                # Plain-language status: what it is doing, why, and whether the car
                # will be ready. See status.py -- kept separate from the technical
                # planner text, which stays available as an attribute.
                "status_headline": charge_status.headline,
                "status_detail": charge_status.detail,
                "status_attributes": charge_status.attributes,
                "outlook": charge_status.outlook,
                "outlook_detail": charge_status.outlook_detail,
                "grid_fallback_at": (
                    charge_status.grid_fallback_at.strftime("%H:%M")
                    if charge_status.grid_fallback_at else None
                ),

                "smart_solar_protection": self.smart_solar_protection,
                # Unwanted-charge watchdog, exposed so a runaway is visible in the
                # UI and in history rather than only in the log.
                "unwanted_charge_reason": unwanted_reason,
                "unwanted_charge_strikes": self.unwanted_charge_strikes,
                "charger_setpoint_readback": (
                    self._read_charger_setpoint(self.config["charger_current_entity"])
                    if self.config.get("charger_current_entity") else None
                ),
                "last_written_current": self.last_written_current,
                # Smart planner diagnostics. Present as None when the planner did
                # not run this cycle (NORMAL/POWER modes, or planner disabled).
                "smart_enabled": use_smart,
                "smart_state": smart_decision.state_name if smart_decision else None,
                "smart_urgency": smart_decision.urgency if smart_decision else None,
                "smart_headroom_w": smart_decision.headroom_w if smart_decision else None,
                "smart_reserve_w": smart_decision.reserve_w if smart_decision else None,
                "smart_share_factor": smart_decision.share_factor if smart_decision else None,
                "smart_capability_w": smart_decision.capability_w if smart_decision else None,
                "smart_trend": smart_decision.trend if smart_decision else None,
                "smart_grid_w": smart_decision.grid_w if smart_decision else None,
                "smart_projected_batt_soc": smart_decision.projected_batt_soc if smart_decision else None,
                "smart_stops_today": smart_decision.stops_today if smart_decision else None,
                "smart_dwell_left_s": smart_decision.dwell_left_s if smart_decision else None,
                "smart_slack": smart_decision.slack if smart_decision else None,
                # Whether the unblock button has anything to do: a pause still
                # running, or a start confirmation mid-count. Lets a card show the
                # control as inert rather than offering a no-op.
                "smart_unblockable": bool(
                    self.smart_state.last_stop is not None
                    or 0 < self.smart_state.start_dwell_s < int(
                        self.config.get("smart_start_dwell_s", 180)
                    )
                    or self.eco_timer_active
                    or self.auto_timer_active
                ),
                "smart_sun": smart_decision.sun_phase if smart_decision else None,
                "smart_sun_reason": smart_decision.sun_reason if smart_decision else None,
                "smart_pv_slope_w_min": smart_decision.pv_slope_w_min if smart_decision else None,
                "house_avg_w": house_avg_w,
                # Deadline and duration diagnostics, present in every mode.
                "next_target_at": charge_debug.next_target_at,
                "critical_at": charge_debug.critical_at,
                "charge_hours_at_reference": charge_debug.charge_hours_at_reference,
                "hours_to_full_now": charge_debug.hours_to_full_now,
                "full_at": charge_debug.full_at or None,
                "debug_attributes": charge_debug.attributes,
            }

            _LOGGER.debug(
                f"Update: PV={pv_power}W, Consumption={house_consumption}W (raw={house_consumption_raw}W), "
                f"EV={ev_power}W, Surplus={surplus}W, Battery={battery_power}W, "
                f"Available={available_current}A, Mode={current_mode}"
            )

            return new_data

        except Exception as e:
            _LOGGER.error(f"Error updating EV Control data: {e}", exc_info=True)
            raise

    def _get_sensor_value(self, entity_id: Optional[str], default: float) -> float:
        """Get value from a sensor entity, with unit conversion if needed.
        
        If sensor is 'unknown' or 'unavailable', returns the last valid value from self.data
        to maintain charging during temporary sensor outages.
        """
        if not entity_id:
            return default

        state = self.hass.states.get(entity_id)
        if state is None:
            _LOGGER.warning(f"Entity {entity_id} not found")
            return default

        # Check if state is unknown or unavailable - use last valid value
        if state.state.lower() in ["unknown", "unavailable"]:
            _LOGGER.warning(f"Entity {entity_id} is {state.state}, using last valid value")
            # Try to get the last value from data based on entity_id
            if self.data:
                # Map entity suffixes to data keys
                entity_key_map = {
                    "pv_power_entity": "pv_power",
                    "consumption_entity": "house_consumption",
                    "battery_soc_entity": "battery_soc",
                    "voltage_entity": "voltage",
                    "ev_soc_entity": "ev_soc",
                    "total_consumption_entity": "total_consumption",
                }
                # Find which config key this entity_id corresponds to
                for config_key, data_key in entity_key_map.items():
                    if self.config.get(config_key) == entity_id and data_key in self.data:
                        last_value = self.data.get(data_key, default)
                        _LOGGER.debug(f"Using last valid {data_key}: {last_value}")
                        return last_value
            return default

        try:
            value = float(state.state)
            
            # Try to get unit from attributes and convert if needed
            unit = state.attributes.get("unit_of_measurement", "")
            
            # Common conversions
            if unit in ["kW", "kilowatt"]:
                value = value * 1000  # Convert to W
            elif unit in ["MW", "megawatt"]:
                value = value * 1000000  # Convert to W
            
            return value
        except (ValueError, TypeError):
            _LOGGER.warning(f"Could not convert state of {entity_id}: {state.state}")
            return default

    def _calculate_ev_power_from_charger(self, voltage: float) -> float:
        """Calculate EV power from charger current (charger_current × voltage).
        
        Returns the MAX between:
        - Calculated power: charger_current × voltage
        - Sensor power (if available): actual power from charger sensor
        
        Returns 0 if charger is not actively charging.
        If charger current entity is unknown/unavailable, uses last valid EV power.
        This is more accurate than using a power sensor alone which may lag.
        """
        # Check charger status first
        charger_status_entity = self.config.get("charger_status_entity")
        if charger_status_entity:
            status_state = self.hass.states.get(charger_status_entity)
            if status_state is not None:
                status = status_state.state.lower()
                if status != "charging":
                    _LOGGER.debug(f"Charger status is {status}, EV power = 0W")
                    return 0.0
        
        # Get current charger current entity (the one we're controlling)
        charger_current_entity = self.config.get("charger_current_entity")
        if not charger_current_entity:
            _LOGGER.debug("No charger_current_entity configured, EV power = 0W")
            return 0.0
        
        charger_current_state = self.hass.states.get(charger_current_entity)
        if charger_current_state is None:
            _LOGGER.debug(f"Charger current entity {charger_current_entity} not found")
            return 0.0
        
        # Check if state is unknown or unavailable - use last valid EV power
        if charger_current_state.state.lower() in ["unknown", "unavailable"]:
            _LOGGER.warning(f"Charger current entity {charger_current_entity} is {charger_current_state.state}, using last valid EV power")
            if self.data:
                last_ev_power = self.data.get("ev_power", 0.0)
                _LOGGER.debug(f"Using last valid EV power: {last_ev_power}W")
                return last_ev_power
            return 0.0
        
        try:
            charger_current = float(charger_current_state.state)
            calculated_power = charger_current * voltage
            _LOGGER.debug(f"EV power calculated from current: {charger_current}A × {voltage}V = {calculated_power}W")
            
            # Use ONLY the calculated power from charger_current × voltage
            # The charger_power_entity sensor is often stale and causes on/off cycling
            # when charge state changes (e.g. just starting to charge, sensor hasn't updated yet)
            return calculated_power
            
        except (ValueError, TypeError):
            _LOGGER.warning(f"Could not parse charger current: {charger_current_state.state}, using last valid EV power")
            if self.data:
                last_ev_power = self.data.get("ev_power", 0.0)
                return last_ev_power
            return 0.0

    def _get_validated_ev_power(self, ev_power_charger: float, tolerance_percent: float = 30.0) -> float:
        """Get validated EV power using auto-reported power when available.
        
        Uses power reported by the EV (from MQTT via ev_power_auto_entity) when:
        - Entity is configured
        - Value is not None/unavailable
        - Value is "reasonable" (within tolerance of charger-calculated power)
        
        Falls back to charger power with 7% loss compensation if:
        - Auto power entity not configured
        - Auto power is unavailable/null
        - Auto power differs too much from charger power (indicates stale/wrong data)
        
        Tolerance: 30% (was 20%) - real-world measurements show 20-30% variance due to:
        - MQTT timing delays between charger and EV reports
        - Sensor approximation differences
        - Variable transmission losses (7% nominal + line losses)
        - Different update frequencies (charger 1x/sec, EV 1x/min or less)
        """
        # Check if ev_power_auto_entity is configured
        ev_power_auto_entity = self.config.get("ev_power_auto_entity")
        if not ev_power_auto_entity:
            # No auto power entity, use charger power with 7% loss compensation
            validated_power = ev_power_charger * 0.93
            _LOGGER.debug(f"No ev_power_auto_entity configured, using charger power with 7% loss: {ev_power_charger}W → {validated_power:.1f}W")
            return validated_power
        
        # Try to get auto power value
        auto_power_state = self.hass.states.get(ev_power_auto_entity)
        if auto_power_state is None:
            _LOGGER.warning(f"ev_power_auto_entity {ev_power_auto_entity} not found, falling back to charger power with 7% loss")
            return ev_power_charger * 0.93
        
        # Check if state is unavailable or unknown
        if auto_power_state.state.lower() in ["unknown", "unavailable", "none", "null"]:
            _LOGGER.debug(f"ev_power_auto_entity is {auto_power_state.state}, using charger power with 7% loss")
            return ev_power_charger * 0.93
        
        try:
            auto_power = float(auto_power_state.state)
            
            # Handle unit conversion if needed
            unit = auto_power_state.attributes.get("unit_of_measurement", "")
            if unit in ["kW", "kilowatt"]:
                auto_power = auto_power * 1000
            elif unit in ["MW", "megawatt"]:
                auto_power = auto_power * 1000000
            
            # If auto power is 0 but charger is charging, it's stale/wrong data
            if auto_power == 0 and ev_power_charger > 0:
                _LOGGER.debug(f"Auto power is 0W but charger shows {ev_power_charger}W, likely stale data, using charger with 7% loss")
                return ev_power_charger * 0.93
            
            # If auto power is 0 and charger is also 0, both agree - no charging
            if auto_power == 0 and ev_power_charger == 0:
                _LOGGER.debug(f"Both auto and charger power are 0W - not charging")
                return 0.0
            
            # Validate auto power is within tolerance of charger power
            # Tolerance increased to 30% to account for real-world measurement variance
            if ev_power_charger > 0:
                difference_percent = abs(auto_power - ev_power_charger) / ev_power_charger * 100
                
                if difference_percent <= tolerance_percent:
                    # Auto power is valid and reasonable
                    _LOGGER.debug(f"Auto power valid: {auto_power:.0f}W (charger={ev_power_charger:.0f}W, diff={difference_percent:.1f}%, tolerance={tolerance_percent}%)")
                    return auto_power
                else:
                    # Auto power differs too much - likely stale/incorrect
                    # Only warn if difference is severe (> 35%), otherwise debug
                    if difference_percent > 35.0:
                        _LOGGER.warning(f"Auto power {auto_power:.0f}W differs from charger {ev_power_charger:.0f}W by {difference_percent:.1f}% (tolerance={tolerance_percent}%), SEVERE MISMATCH, using charger with 7% loss")
                    else:
                        _LOGGER.debug(f"Auto power {auto_power:.0f}W differs from charger {ev_power_charger:.0f}W by {difference_percent:.1f}% (tolerance={tolerance_percent}%), within normal variance, using charger with 7% loss")
                    return ev_power_charger * 0.93
            else:
                # Charger power is 0, can't validate against it
                # If auto power is high, it's likely stale - use with caution
                if auto_power > 1000:  # Only use if significant (>1kW)
                    _LOGGER.debug(f"Charger shows 0W but auto shows {auto_power:.0f}W - using auto power with caution (may be stale)")
                    return auto_power
                else:
                    _LOGGER.debug(f"Both charger and auto power are near 0W")
                    return 0.0
                    
        except (ValueError, TypeError):
            _LOGGER.warning(f"Could not parse auto power value: {auto_power_state.state}, using charger power with 7% loss")
            return ev_power_charger * 0.93

    def set_mode(self, mode: str) -> None:
        """Set charging mode."""
        if mode not in ["eco", "normal", "power", "auto", "auto-eco"]:
            _LOGGER.error(f"Invalid mode: {mode}")
            return
        if self.data:
            self.data["mode"] = mode
        
        # Save mode to persistent storage
        self.hass.create_task(self._async_save_state(mode=mode))
        
        # Reset ECO timer when switching to ECO mode
        if mode == "eco":
            self.eco_timer_seconds = 0
            self.eco_last_state = "stopped"  # Wait for a real solar-charging cycle before arming hold
            self.eco_timer_active = False
            _LOGGER.info(f"Mode changed to {mode} - ECO timer reset")
        
        # Reset AUTO timer when switching to AUTO/AUTO-ECO mode
        if mode in ["auto", "auto-eco"]:
            self.auto_timer_seconds = 0
            self.auto_last_state = "stopped"  # Wait for a real solar-charging cycle before arming hold
            self.auto_timer_active = False
            _LOGGER.info(f"Mode changed to {mode} - AUTO timer reset")
        
        if mode not in ["eco"] and mode not in ["auto", "auto-eco"]:
            _LOGGER.info(f"Mode changed to {mode}")

    def set_base_current(self, current: float) -> None:
        """Set base current for normal mode."""
        if self.data:
            self.data["base_current"] = current
        # Save base_current to persistent storage
        self.hass.create_task(self._async_save_state(base_current=current))
        _LOGGER.info(f"Base current set to {current}A")

    def set_grid_power_limit(self, power_kw: float) -> None:
        """Set grid power limit."""
        if self.data:
            self.data["grid_power_limit"] = power_kw
        # Save grid_power_limit to persistent storage
        self.hass.create_task(self._async_save_state(grid_power_limit=power_kw))
        _LOGGER.info(f"Grid power limit set to {power_kw}kW")

    def set_battery_reservation(self, power_watts: float) -> None:
        """Set battery reservation power."""
        if self.data:
            self.data["battery_reservation_power"] = power_watts
        # Save battery_reservation_power to persistent storage
        self.hass.create_task(self._async_save_state(battery_reservation_power=power_watts))
        _LOGGER.info(f"Battery reservation power set to {power_watts}W")

    def set_charging_enabled(self, enabled: bool) -> None:
        """Enable or disable charging."""
        if self.data:
            self.data["charging_enabled"] = enabled
        self.hass.create_task(self._async_save_state(charging_enabled=enabled))
        _LOGGER.info(f"Charging {'enabled' if enabled else 'disabled'}")

    def _rebuild_target_schedule(self) -> None:
        """Read `target_schedule_<day>` out of the config into self.target_schedule.

        Empty strings become None: the planner treats a missing day as "no deadline
        today", and "" would read as a truthy-looking key that then fails to parse.
        """
        for day in SCHEDULE_DAYS:
            time_str = self.config.get(f"target_schedule_{day}", "")
            self.target_schedule[day] = time_str.strip() if time_str and time_str.strip() else None
            # Kept in its own dict rather than folded into target_schedule, so
            # clearing a deadline does not silently discard the percentage the
            # user had chosen for that day.
            self.target_soc_schedule[day] = int(
                resolve_target_soc(
                    {day: self.config.get(f"target_soc_{day}")}, day
                )
            )

    def apply_target_schedule(self, schedule: Dict[str, Optional[str]]) -> None:
        """Apply a saved schedule without reloading the entry.

        The options flow reaches the same values through a config-entry update, which
        reloads the integration. That is fine when you are already sitting in a dialog,
        but a reload also discards the planner session, the rolling house/PV averages
        and the write deadband -- too much to throw away because someone moved
        tomorrow's deadline by half an hour from the card. So the config dict and the
        parsed schedule are patched in place instead, and the reload is suppressed by
        the caller in __init__.py.
        """
        for day, value in schedule.items():
            if day in SCHEDULE_DAYS:
                self.config[f"target_schedule_{day}"] = value or ""
        self._rebuild_target_schedule()
        if self.data is not None:
            self.data["target_schedule"] = dict(self.target_schedule)
        _LOGGER.info("Target schedule updated: %s", self.target_schedule)

    def apply_target_soc(self, schedule: Dict[str, Optional[int]]) -> None:
        """Apply saved target SOCs without reloading the entry.

        The companion of `apply_target_schedule`, and in place for the same
        reason: nudging tomorrow's percentage from the card must not cost the
        planner its session, its rolling averages and its write deadband.
        """
        for day, value in schedule.items():
            if day in SCHEDULE_DAYS:
                self.config[f"target_soc_{day}"] = value or ""
        self._rebuild_target_schedule()
        if self.data is not None:
            self.data["target_soc_schedule"] = dict(self.target_soc_schedule)
        _LOGGER.info("Target SOC updated: %s", self.target_soc_schedule)

    def unblock_now(self) -> list:
        """Drop the planner's self-imposed waits so a start can happen this cycle.

        Two timers stand between "conditions are fine" and "charging", and both
        exist to protect the contactor rather than to answer any physical limit:

        - the minimum-off pause after a stop (`smart_min_off_min`), which is what
          produces "Non riparto prima delle 23:30";
        - the start confirmation dwell (`smart_start_dwell_s`), which waits to see
          whether a surplus is real before closing the contactor.

        This clears both. It deliberately does *not* start charging, raise the
        current, or touch the mode: if there is no surplus and no deadline
        pressure, the planner will simply decide not to start, and say so. What it
        removes is the waiting, not the judgement.

        Left alone on purpose, because they are not waits:
        - the daily stop budget (`smart_max_stops_per_day`),
        - the home-battery floor (`solar_min_soc`),
        - the sunset guard.

        Returns the list of things actually cleared, for the log and the caller.
        """
        cleared = []

        if self.smart_state.last_stop is not None:
            self.smart_state.last_stop = None
            cleared.append("pausa minima dopo lo stop")

        # Setting the accumulator to the target means the next cycle's increment
        # takes it past, so the start happens then rather than after another full
        # dwell. Jumping straight to "charging" here would bypass the surplus
        # check too, which is the one judgement that has to stay.
        dwell_target = int(self.config.get("smart_start_dwell_s", 180))
        if self.smart_state.start_dwell_s < dwell_target:
            self.smart_state.start_dwell_s = float(dwell_target)
            cleared.append("conferma di avvio")

        # The legacy timers only run with the smart planner switched off, but a
        # button called "unblock now" that quietly ignored them there would be
        # lying about what it does.
        if self.eco_timer_active or self.eco_timer_seconds:
            self.eco_timer_active = False
            self.eco_timer_seconds = 0
            cleared.append("timer ECO")
        if self.auto_timer_active:
            self.auto_timer_active = False
            cleared.append("timer AUTO")

        if cleared:
            _LOGGER.info("Unblock requested, cleared: %s", ", ".join(cleared))
        else:
            _LOGGER.info("Unblock requested, but nothing was holding a start back")
        return cleared

    def set_smart_solar_protection(self, enabled: bool) -> None:
        """Enable or disable smart solar protection (anti-cycle for BYD)."""
        self.smart_solar_protection = enabled
        if self.data:
            self.data["smart_solar_protection"] = enabled
        self.hass.create_task(self._async_save_state(smart_solar_protection=enabled))
        _LOGGER.info(f"Smart solar protection {'enabled' if enabled else 'disabled'}")


    async def _async_save_state(self, mode: str = None, base_current: float = None, grid_power_limit: float = None, battery_reservation_power: float = None, charging_enabled: bool = None, smart_solar_protection: bool = None, smart_values: dict = None) -> None:
        """Save runtime state to persistent storage."""
        try:
            data = await self.store.async_load() or {}
            if mode is not None:
                data["mode"] = mode
                _LOGGER.debug(f"Saved mode to storage: {mode}")
            if base_current is not None:
                data["base_current"] = base_current
                _LOGGER.debug(f"Saved base_current to storage: {base_current}A")
            if grid_power_limit is not None:
                data["grid_power_limit"] = grid_power_limit
                _LOGGER.debug(f"Saved grid_power_limit to storage: {grid_power_limit}kW")
            if battery_reservation_power is not None:
                data["battery_reservation_power"] = battery_reservation_power
                _LOGGER.debug(f"Saved battery_reservation_power to storage: {battery_reservation_power}W")
            if charging_enabled is not None:
                data["charging_enabled"] = charging_enabled
                _LOGGER.debug(f"Saved charging_enabled to storage: {charging_enabled}")
            if smart_solar_protection is not None:
                data["smart_solar_protection"] = smart_solar_protection
                _LOGGER.debug(f"Saved smart_solar_protection to storage: {smart_solar_protection}")
            if smart_values:
                data.update(smart_values)
                _LOGGER.debug(f"Saved smart planner values to storage: {smart_values}")
            await self.store.async_save(data)
        except Exception as e:
            _LOGGER.warning(f"Could not save state to storage: {e}")

    # Keys the user can retune live from the dashboard rather than through the
    # options flow. They shadow the configured value when set.
    SMART_RUNTIME_KEYS = (
        "smart_batt_floor_w",
        "solar_stop_pv_w",
        "solar_min_soc",
    )

    def _smart_overrides(self) -> dict:
        """Runtime overrides for the planner config, from the number entities."""
        if not self.data:
            return {}
        return {
            key: self.data[key]
            for key in self.SMART_RUNTIME_KEYS
            if self.data.get(key) is not None
        }

    def set_smart_value(self, key: str, value: float) -> None:
        """Set one of the live-tunable planner values and persist it."""
        if key not in self.SMART_RUNTIME_KEYS:
            _LOGGER.warning(f"Refusing to set unknown smart key {key}")
            return
        if self.data is not None:
            self.data[key] = value
        self.hass.create_task(self._async_save_state(smart_values={key: value}))
        _LOGGER.info(f"Smart planner {key} set to {value}")

    def _measured_ev_power(self, house_only_w: float, fallback_w: float) -> float:
        """EV power measured rather than inferred from the charger setpoint.

        `_calculate_ev_power_from_charger` returns setpoint x voltage, i.e. what we
        asked the car to take. That is not what the car takes: a full, paused or
        preconditioning EV draws far less, and every downstream figure (surplus,
        charging speed, deadline) inherits the error.

        When both a total-load sensor (inverter, EV included) and a house-only meter
        are configured, the difference between them is the real EV draw, from two
        readings of the same installation.
        """
        total_entity = self.config.get("total_consumption_entity")
        if not total_entity:
            return fallback_w

        total_w = self._get_sensor_value(total_entity, None)
        if total_w is None:
            return fallback_w

        measured = total_w - house_only_w
        # A negative or absurd result means the two sensors disagree (different
        # scaling, one stale). Fall back rather than poison the control loop.
        if measured < -200 or measured > self.config.get("charger_max_current", 32) * 250:
            _LOGGER.debug(
                f"Measured EV power {measured:.0f}W out of range "
                f"(total={total_w:.0f}W, house={house_only_w:.0f}W), using {fallback_w:.0f}W"
            )
            return fallback_w

        # A charging car draws at least the charger minimum -- roughly 1.4 kW at
        # 6 A. Anything below that is the wallbox idling plus the disagreement
        # between two meters reading the same installation, and treating it as
        # charging is actively harmful: divided into the energy still needed it
        # produces absurd completion times, and it feeds noise into the measured
        # charging speed. Below the threshold the car is not charging, full stop.
        floor_w = self.config.get("charger_min_current", 6) * self.grid_voltage * 0.5
        if measured < floor_w:
            if measured > 50:
                _LOGGER.debug(
                    f"Measured EV power {measured:.0f}W below the {floor_w:.0f}W charging "
                    f"floor, treating as idle"
                )
            return 0.0
        return measured

    def _read_charger_setpoint(self, entity_id: str) -> Optional[float]:
        """The setpoint the charger currently reports, or None if unreadable.

        Used to tell whether the value we sent is still in force. An unavailable
        entity returns None rather than 0: a missing readback is not evidence of a
        setpoint of zero, and treating it as one would force a write every cycle
        while the wallbox integration is restarting.
        """
        state = self.hass.states.get(entity_id)
        if state is None or state.state.lower() in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    def _independent_ev_power(self, house_only_w: float) -> Optional[tuple]:
        """EV draw from a source that does not depend on the setpoint we commanded.

        Returns (watts, source), or None when no such source is configured -- which
        is the distinction that matters here: `_calculate_ev_power_from_charger`
        returns setpoint x voltage, so comparing it against the setpoint can never
        reveal a disagreement. Only a real measurement can.

        The source name is returned because the two differ in how far they can be
        trusted alone:
        1. "meters" -- total load minus house-only load. Two readings of the same
           installation, refreshed as fast as the inverter polls. Trustworthy.
        2. "ev_report" -- the power the EV itself reports over MQTT. Accurate but
           slow, often one update per minute, so it can still be describing a
           session that has already ended.
        """
        total_entity = self.config.get("total_consumption_entity")
        # Only usable when the house meter really is house-only. With
        # consumption_includes_ev the caller's house figure has already had the
        # setpoint-derived EV power subtracted from it, so total minus house returns
        # that same setpoint -- circular, and it would make this watchdog agree with
        # itself no matter what the hardware is doing.
        if total_entity and not self.config.get("consumption_includes_ev", False):
            total_w = self._get_sensor_value(total_entity, None)
            if total_w is not None:
                measured = total_w - house_only_w
                # Same sanity band as _measured_ev_power: outside it the two sensors
                # disagree about scaling, or one of them is stale.
                if -200 <= measured <= self.config.get("charger_max_current", 32) * 250:
                    return max(0.0, measured), "meters"

        auto_entity = self.config.get("ev_power_auto_entity")
        if auto_entity:
            # Through _get_sensor_value so a sensor reporting kW is converted; a raw
            # float() of the state would be wrong by a factor of 1000.
            auto_w = self._get_sensor_value(auto_entity, None)
            if auto_w is not None:
                return max(0.0, auto_w), "ev_report"

        return None

    def _charger_reports_charging(self) -> bool:
        """Whether the wallbox itself says a session is live."""
        status_entity = self.config.get("charger_status_entity")
        if not status_entity:
            return False
        state = self.hass.states.get(status_entity)
        if state is None:
            return False
        return state.state.lower() == "charging"

    def _detect_unwanted_charge(
        self, commanded_a: float, house_only_w: float, voltage: float
    ) -> Optional[str]:
        """Return a reason string when the car draws more than we asked for.

        This is the case the control loop was blind to: the setpoint says 0A, the
        wallbox charges anyway, and because EV power is *derived from that setpoint*
        every downstream figure agrees that nothing is happening. The grid pays for
        it until somebody notices by hand.

        The evidence has to be independent of the setpoint, so a measured power
        source is required. Without one there is nothing to compare, and this returns
        None rather than guessing: the charger status alone goes to "charging" during
        the handshake and would cry wolf on every normal session start.

        Debounced over several cycles, because meters lag a ramp-down by seconds and
        one cycle of disagreement is expected rather than a fault. When the only
        source is the slow EV report, the charger status has to confirm as well --
        a minute-old "7 kW" is not evidence about now.
        """
        measurement = self._independent_ev_power(house_only_w)
        if measurement is None:
            self.unwanted_charge_strikes = 0
            return None
        measured_w, source = measurement

        commanded_w = max(0.0, commanded_a) * voltage
        overdraw_w = measured_w - commanded_w

        if commanded_a <= 0:
            # Commanded stop. Any real draw is unwanted, full stop.
            is_unwanted = measured_w >= UNWANTED_CHARGE_FLOOR_W
            detail = f"commanded 0A but measuring {measured_w:.0f}W via {source}"
        else:
            # Commanded some current. Flag only a draw well above it: a car taking
            # *less* than offered is normal and happens constantly.
            is_unwanted = overdraw_w >= UNWANTED_CHARGE_FLOOR_W
            detail = (
                f"commanded {commanded_a:.0f}A (~{commanded_w:.0f}W) but measuring "
                f"{measured_w:.0f}W via {source}, {overdraw_w:.0f}W over"
            )

        # The EV report is too slow to convict on its own; require the wallbox to
        # agree that a session is live.
        if is_unwanted and source == "ev_report" and not self._charger_reports_charging():
            _LOGGER.debug(
                f"Ignoring possible unwanted charge ({detail}): charger status does "
                f"not report charging and the EV report lags"
            )
            self.unwanted_charge_strikes = 0
            return None

        if not is_unwanted:
            if self.unwanted_charge_strikes:
                _LOGGER.debug(f"Unwanted-charge strikes reset ({detail}, within tolerance)")
            self.unwanted_charge_strikes = 0
            return None

        self.unwanted_charge_strikes += 1
        if self.unwanted_charge_strikes < UNWANTED_CHARGE_STRIKES:
            _LOGGER.debug(
                f"Possible unwanted charge ({detail}), strike "
                f"{self.unwanted_charge_strikes}/{UNWANTED_CHARGE_STRIKES}"
            )
            return None

        if self._charger_reports_charging():
            detail += ", charger status=charging"
        return f"UNWANTED CHARGE: {detail}"

    async def _enforce_stop(self, reason: str) -> None:
        """Press the stop button when a 0A setpoint is not being honoured.

        Rewriting 0A is not always enough: some wallboxes read 0 as "no change" or
        clamp it to their own minimum and keep the contactor closed. The stop button
        is the only command that definitely ends the session, and until now nothing
        but a human could press it.
        """
        if not self.config.get("charger_stop_entity"):
            _LOGGER.error(
                f"{reason} -- no charger_stop_entity configured, cannot force a stop"
            )
            return

        now = datetime.now()
        if self.last_stop_press is not None:
            since = (now - self.last_stop_press).total_seconds()
            if since < STOP_PRESS_COOLDOWN_SECONDS:
                _LOGGER.warning(
                    f"{reason} -- stop already pressed {since:.0f}s ago, waiting out the "
                    f"{STOP_PRESS_COOLDOWN_SECONDS:.0f}s cooldown"
                )
                return

        _LOGGER.error(f"{reason} -- pressing stop to end the session")
        try:
            await self.stop_charging()
            self.last_stop_press = now
        except Exception as e:
            _LOGGER.error(f"Failed to press stop after unwanted charge: {e}")

    def _daylight_hours_left(self, now: datetime) -> float:
        """Hours of daylight remaining today, from the core `sun.sun` entity."""
        sun = self.hass.states.get("sun.sun")
        if sun is None:
            return 0.0
        if sun.state != "above_horizon":
            return 0.0
        next_setting = sun.attributes.get("next_setting")
        if not next_setting:
            return 0.0
        try:
            setting = dt_util.parse_datetime(str(next_setting))
            if setting is None:
                return 0.0
            delta = setting - dt_util.as_local(dt_util.as_utc(now))
            return max(0.0, delta.total_seconds() / 3600.0)
        except (ValueError, TypeError) as err:
            _LOGGER.debug(f"Could not parse sun.sun next_setting: {err}")
            return 0.0

    def _read_forecast(self, now: datetime, house_avg_w: float, pv_avg_w: float) -> ForecastSignals:
        """Assemble the Solcast forward view.

        Every entity is optional. `valid` is only set when the three that actually
        drive decisions are present, so a Solcast outage degrades the planner to
        pure closed-loop control rather than making it act on zeros.
        """
        remaining = self._get_sensor_value(self.config.get("solcast_remaining_today_entity"), None)
        tomorrow = self._get_sensor_value(self.config.get("solcast_tomorrow_entity"), None)
        power_now = self._get_sensor_value(self.config.get("solcast_power_now_entity"), None)
        power_30 = self._get_sensor_value(self.config.get("solcast_power_30min_entity"), None)
        power_1h = self._get_sensor_value(self.config.get("solcast_power_1h_entity"), None)

        valid = remaining is not None and power_now is not None and power_1h is not None
        if not valid:
            _LOGGER.debug("Solcast forecast unavailable, planner runs closed-loop only")

        return ForecastSignals(
            valid=valid,
            pv_now_w=power_now or 0.0,
            pv_30min_w=power_30 if power_30 is not None else (power_now or 0.0),
            pv_1h_w=power_1h or 0.0,
            remaining_today_kwh=remaining or 0.0,
            tomorrow_kwh=tomorrow or 0.0,
            daylight_hours_left=self._daylight_hours_left(now),
            house_avg_w=house_avg_w,
            pv_avg_w=pv_avg_w,
        )

    async def start_charging(self) -> None:
        """Send start command to charger."""
        button_entity = self.config.get("charger_start_entity")
        if button_entity:
            await self.hass.services.async_call(
                "button",
                "press",
                {"entity_id": button_entity},
            )
            _LOGGER.info(f"Start charging button pressed: {button_entity}")

    async def stop_charging(self) -> None:
        """Send stop command to charger."""
        button_entity = self.config.get("charger_stop_entity")
        if button_entity:
            await self.hass.services.async_call(
                "button",
                "press",
                {"entity_id": button_entity},
            )
            _LOGGER.info(f"Stop charging button pressed: {button_entity}")
