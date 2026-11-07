"""Configuration flow for EV Control.

Both flows -- first-time setup and later reconfiguration -- are built from the same
schema builders below. Duplicating them was how the two drifted apart: several
fields existed in one and not the other, and a couple carried different defaults.

The options flow is a menu rather than a wizard. Reconfiguring one value used to
mean reopening the dialog, choosing a section from a dropdown, submitting it, and
then starting the whole thing over for the next section. Now the menu stays put:
you step into a section, submit, and land back on the menu, with your edits held
until you choose to save them.
"""
import logging
from typing import Any, Dict, Optional
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.const import UnitOfElectricCurrent, UnitOfPower

from .helpers import default_grid_margin_w, validate_control

_LOGGER = logging.getLogger(__name__)
DOMAIN = "evcontrol"

# 15-minute time slots for schedule dropdowns
_TIME_OPTIONS = [""] + [
    f"{h:02d}:{m:02d}" for h in range(24) for m in range(0, 60, 15)
]

_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

# Solcast publishes these under stable object_ids on an Italian-language install.
# They are only suggestions: every field is a free entity picker.
_SOLCAST_SUGGESTIONS = {
    "solcast_remaining_today_entity": "sensor.solcast_pv_forecast_previsione_rimanente_per_oggi",
    "solcast_tomorrow_entity": "sensor.solcast_pv_forecast_previsione_per_domani",
    "solcast_power_now_entity": "sensor.solcast_pv_forecast_potenza_attuale",
    "solcast_power_30min_entity": "sensor.solcast_pv_forecast_potenza_tra_30_minuti",
    "solcast_power_1h_entity": "sensor.solcast_pv_forecast_potenza_tra_1_ora",
}


# ── Field helpers ────────────────────────────────────────────────────────────
# `d` is the current configuration: empty during first-time setup, populated when
# reconfiguring. Passing the current value as `suggested_value` rather than as a
# voluptuous `default` matters for optional fields: a suggestion pre-fills the box
# but still lets the X button clear it, whereas a default would silently restore
# the old value every time the field was emptied.

def _entity(schema: dict, d: dict, key: str, domain: str, required: bool = True,
            suggestion: Optional[str] = None) -> None:
    """Add an entity picker bound to `key`."""
    current = d.get(key) or suggestion
    if required:
        marker = vol.Required(key, description={"suggested_value": current})
    else:
        # default=None so clearing the field actually removes the sensor instead of
        # falling through to the previously saved value.
        marker = vol.Optional(key, default=None,
                              description={"suggested_value": current})
    schema[marker] = EntitySelector(EntitySelectorConfig(domain=domain))


def _slider(schema: dict, d: dict, key: str, fallback, minimum, maximum,
            step=1, unit=None) -> None:
    """Add a slider. Sliders suit values with a natural range you scrub through."""
    cfg: Dict[str, Any] = {"min": minimum, "max": maximum, "step": step,
                           "mode": NumberSelectorMode.SLIDER}
    if unit:
        cfg["unit_of_measurement"] = unit
    value = d.get(key)
    schema[vol.Required(key, default=fallback if value is None else value)] = \
        NumberSelector(NumberSelectorConfig(**cfg))


def _box(schema: dict, d: dict, key: str, fallback, minimum, maximum,
         step=1, unit=None) -> None:
    """Add a typed number box, for values you set once to a precise figure."""
    cfg: Dict[str, Any] = {"min": minimum, "max": maximum, "step": step,
                           "mode": NumberSelectorMode.BOX}
    if unit:
        cfg["unit_of_measurement"] = unit
    value = d.get(key)
    schema[vol.Required(key, default=fallback if value is None else value)] = \
        NumberSelector(NumberSelectorConfig(**cfg))


def _toggle(schema: dict, d: dict, key: str, fallback: bool) -> None:
    value = d.get(key)
    schema[vol.Required(key, default=fallback if value is None else value)] = \
        BooleanSelector()


# ── Section schemas ──────────────────────────────────────────────────────────

def _inverter_schema(d: dict) -> dict:
    s: dict = {}
    _entity(s, d, "pv_power_entity", "sensor")
    _entity(s, d, "voltage_entity", "sensor")
    return s


def _consumption_schema(d: dict) -> dict:
    s: dict = {}
    _entity(s, d, "consumption_entity", "sensor")
    _toggle(s, d, "consumption_includes_ev", False)
    # Total load including the EV (typically the inverter's own load figure). With
    # this AND a house-only meter, the EV's real draw is the difference between
    # them, instead of being inferred from the charger setpoint.
    _entity(s, d, "total_consumption_entity", "sensor", required=False)
    return s


def _battery_schema(d: dict) -> dict:
    s: dict = {}
    _entity(s, d, "battery_soc_entity", "sensor")
    _entity(s, d, "battery_power_entity", "sensor", required=False)
    _box(s, d, "home_battery_capacity_kwh", 10, 1, 100, 0.5, "kWh")
    _slider(s, d, "battery_min_soc", 20, 0, 50, 1, "%")
    _slider(s, d, "battery_start_charge_soc", 70, 0, 100, 1, "%")
    _slider(s, d, "battery_max_drain_power", 2500, 500, 10000, 100, UnitOfPower.WATT)
    _slider(s, d, "battery_reservation_power", 500, 0, 2000, 50, UnitOfPower.WATT)
    return s


def _charger_schema(d: dict) -> dict:
    s: dict = {}
    _entity(s, d, "charger_current_entity", "number")
    _entity(s, d, "charger_start_entity", "button")
    _entity(s, d, "charger_stop_entity", "button")
    _slider(s, d, "charger_min_current", 6, 1, 32, 1, UnitOfElectricCurrent.AMPERE)
    _slider(s, d, "charger_max_current", 32, 6, 63, 1, UnitOfElectricCurrent.AMPERE)
    _entity(s, d, "charger_status_entity", "sensor", required=False)
    # charger_power_entity was asked for and then never read: the control loop
    # deliberately ignores it, because it lags reality and used to cause cycling.
    return s


def _ev_vehicle_schema(d: dict) -> dict:
    s: dict = {}
    _entity(s, d, "ev_soc_entity", "sensor", required=False)
    # ev_charging_speed_entity was read into the state dict and never used in any
    # calculation: the charging speed is measured from SOC and power instead.
    _entity(s, d, "ev_power_auto_entity", "sensor", required=False)
    _box(s, d, "ev_battery_capacity_kwh", 42, 10, 200, 0.5, "kWh")
    return s


def _schedule_schema(d: dict) -> dict:
    """Return schedule fields with current defaults: a time and a target SOC per day.

    The time uses default="" so if the field is absent from user_input (X button
    clears it), voluptuous falls back to "" instead of restoring the old saved
    value. The current saved value is passed as suggested_value so the UI
    pre-selects it without it becoming the voluptuous fallback.

    The percentage is a plain required box defaulting to 100, because unlike the
    time it has no meaningful empty state: a day either charges to 100% or to
    something less, and an unset percentage means the former.
    """
    options = [{"value": t, "label": t if t else "— nessuno —"} for t in _TIME_OPTIONS]
    schema: dict = {}
    for day in _DAYS:
        schema[vol.Optional(
            f"target_schedule_{day}",
            default="",
            description={"suggested_value": d.get(f"target_schedule_{day}") or ""},
        )] = SelectSelector(SelectSelectorConfig(
            options=options,
            mode=SelectSelectorMode.DROPDOWN,
            # The dropdown offers quarter-hours, but evcontrol.set_schedule (and so
            # the Lovelace card) accepts any minute. Without custom_value a time
            # saved that way would not be in the list, so this dialog would show the
            # day as empty and quietly wipe it on the next save.
            custom_value=True,
        ))
        _box(schema, d, f"target_soc_{day}", 100, 5, 100, 5, "%")
    return schema


def _normalise_schedule(user_input: dict) -> dict:
    """Ensure every day key is present and normalised.

    Times become '' when empty or None; percentages become a whole number, 100
    when the field is missing -- which is what a schedule saved before target SOC
    existed looks like.
    """
    out: dict = {}
    for day in _DAYS:
        out[f"target_schedule_{day}"] = user_input.get(f"target_schedule_{day}") or ""
        raw = user_input.get(f"target_soc_{day}")
        try:
            soc = round(float(raw))
        except (TypeError, ValueError):
            soc = 100
        out[f"target_soc_{day}"] = min(100, max(5, soc))
    return out


def _auto_schema(d: dict) -> dict:
    s: dict = {}
    _slider(s, d, "automatic_charge_current", 15, 6, 32, 1, UnitOfElectricCurrent.AMPERE)
    _slider(s, d, "auto_phase1_soc_threshold", 50, 10, 80, 1, "%")
    return s


def _smart_schema(d: dict) -> dict:
    """The solar planner. The rule is three lines; most of this is set once.

      start   there is enough surplus to run the charger at its minimum
      hold    keep that minimum for as long as the panels produce anything
      stop    production is over, or the home battery is below its floor

    `solar_min_soc` and `solar_stop_pv_w` are the two stop conditions and the only
    values worth revisiting. `smart_batt_floor_w` governs how far *above* the
    minimum the car climbs, not whether it starts.
    """
    s: dict = {}
    _toggle(s, d, "smart_enabled", True)
    _slider(s, d, "solar_min_soc", 60, 0, 100, 1, "%")
    _box(s, d, "solar_stop_pv_w", 400, 0, 3000, 50, UnitOfPower.WATT)
    _box(s, d, "smart_batt_floor_w", 500, 0, 3000, 50, UnitOfPower.WATT)
    _box(s, d, "smart_batt_max_reserve_w", 2500, 0, 6000, 100, UnitOfPower.WATT)
    _box(s, d, "smart_export_target_w", 0, 0, 2000, 50, UnitOfPower.WATT)
    _box(s, d, "smart_import_deadband_w", 200, 0, 2000, 50, UnitOfPower.WATT)
    _box(s, d, "smart_max_stops_per_day", 3, 1, 20, 1)
    _box(s, d, "smart_min_off_min", 20, 0, 240, 5, "min")
    _box(s, d, "smart_start_dwell_s", 180, 0, 1800, 30, "s")
    _box(s, d, "smart_stop_dwell_s", 300, 0, 3600, 30, "s")
    _slider(s, d, "smart_batt_share_soc_low", 60, 0, 100, 1, "%")
    _slider(s, d, "smart_batt_share_soc_high", 90, 0, 100, 1, "%")
    _box(s, d, "smart_untouchable_slack", 3.0, 1, 20, 0.5)
    _box(s, d, "smart_tight_slack", 1.5, 1, 5, 0.1)
    _box(s, d, "smart_ramp_up_a", 1, 1, 8, 1, UnitOfElectricCurrent.AMPERE)
    _box(s, d, "smart_ramp_down_a", 2, 1, 8, 1, UnitOfElectricCurrent.AMPERE)
    _box(s, d, "smart_headroom_ema_s", 60, 0, 600, 10, "s")
    _box(s, d, "smart_capability_window_min", 15, 1, 60, 1, "min")
    return s


def _smart_forecast_schema(d: dict) -> dict:
    """Solcast entities. All optional.

    Without them the planner still works, purely closed-loop: it just loses the
    ability to refuse a start because production is about to fall, and to tell
    whether the home battery will fill up on its own before sunset.
    """
    s: dict = {}
    for key, suggestion in _SOLCAST_SUGGESTIONS.items():
        _entity(s, d, key, "sensor", required=False, suggestion=suggestion)
    return s


def _control_schema(d: dict) -> dict:
    s: dict = {}
    _slider(s, d, "grid_max_current_amps", 32, 6, 100, 1, "A")
    _slider(s, d, "grid_voltage", 230, 220, 400, 10, "V")
    # Default follows the contract: see helpers.default_grid_margin_w for why a
    # flat figure cannot serve both a 3 kW supply and a 10 kW one.
    limit_w = (float(d.get("grid_max_current_amps", 32))
               * float(d.get("grid_voltage", 230)))
    _slider(s, d, "grid_safety_margin_w", round(default_grid_margin_w(limit_w)),
            0, 4000, 100, "W")
    _slider(s, d, "refresh_rate", 20, 5, 120, 5, "s")
    # grid_power_entity is gone: the planner derives the grid exchange from the
    # energy balance, which is exact and unambiguous in sign, and the sensor was
    # only ever written to a debug log.
    return s


# Every section, in the order the setup wizard walks them. The label is what the
# reconfiguration menu shows; the builder is shared by both flows.
_SECTIONS = [
    ("inverter",       "Fotovoltaico",                    _inverter_schema),
    ("consumption",    "Consumo casa",                    _consumption_schema),
    ("battery",        "Batteria di casa",                _battery_schema),
    ("charger",        "Colonnina",                       _charger_schema),
    ("ev_vehicle",     "Veicolo elettrico",               _ev_vehicle_schema),
    ("schedule",       "Orari di ricarica",               _schedule_schema),
    ("auto",           "Modalita AUTO",                   _auto_schema),
    ("smart",          "Ricarica intelligente",           _smart_schema),
    ("smart_forecast", "Previsione solare (Solcast)",     _smart_forecast_schema),
    ("control",        "Rete e aggiornamento",            _control_schema),
]

_BUILDERS = {step: builder for step, _, builder in _SECTIONS}

# Which config keys each section owns, so the menu can mark the ones holding edits.
# Schema keys are voluptuous Markers; `.schema` is the plain string underneath.
_SECTION_KEYS = {
    step: {getattr(k, "schema", k) for k in builder({})}
    for step, _, builder in _SECTIONS
}


class EVControlConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """First-time setup: walks the sections in order."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return EVControlOptionsFlow()

    def __init__(self):
        super().__init__()
        self._data: dict = {}

    async def async_step_user(self, user_input=None):
        if user_input is not None:
            return await self._async_section(0)
        return self.async_show_form(step_id="user", data_schema=vol.Schema({}))

    async def _async_section(self, index: int, user_input=None):
        """Show section `index`, or move on once it has been submitted."""
        step_id, _, builder = _SECTIONS[index]

        if user_input is not None:
            if step_id == "schedule":
                user_input = _normalise_schedule(user_input)
            if step_id == "control":
                error = validate_control(user_input)
                if error:
                    return self.async_show_form(
                        step_id=step_id,
                        data_schema=vol.Schema(builder({**self._data, **user_input})),
                        errors={"base": error},
                    )
            self._data.update(user_input)
            if index + 1 < len(_SECTIONS):
                return await self._async_section(index + 1)
            return self.async_create_entry(title="EV Control", data=self._data)

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(builder({})),
        )

    # Home Assistant dispatches by step id, so each section needs a named handler.
    # They all funnel into _async_section, which owns the ordering.
    async def async_step_inverter(self, user_input=None):
        return await self._async_section(0, user_input)

    async def async_step_consumption(self, user_input=None):
        return await self._async_section(1, user_input)

    async def async_step_battery(self, user_input=None):
        return await self._async_section(2, user_input)

    async def async_step_charger(self, user_input=None):
        return await self._async_section(3, user_input)

    async def async_step_ev_vehicle(self, user_input=None):
        return await self._async_section(4, user_input)

    async def async_step_schedule(self, user_input=None):
        return await self._async_section(5, user_input)

    async def async_step_auto(self, user_input=None):
        return await self._async_section(6, user_input)

    async def async_step_smart(self, user_input=None):
        return await self._async_section(7, user_input)

    async def async_step_smart_forecast(self, user_input=None):
        return await self._async_section(8, user_input)

    async def async_step_control(self, user_input=None):
        return await self._async_section(9, user_input)


class EVControlOptionsFlow(config_entries.OptionsFlow):
    """Reconfiguration: a menu you keep returning to, not a wizard you restart."""

    def __init__(self):
        self._pending: dict = {}

    # ── Menu ─────────────────────────────────────────────────────────────
    async def async_step_init(self, user_input=None):
        """The hub. Every section returns here instead of closing the dialog."""
        menu = {}
        for step_id, label, _ in _SECTIONS:
            # A dot marks a section holding edits that are not written yet, so it
            # is obvious what is waiting on the Save at the bottom.
            touched = bool(_SECTION_KEYS[step_id] & self._pending.keys())
            menu[step_id] = f"● {label}" if touched else label

        if self._pending:
            menu["save"] = f"Salva e chiudi ({len(self._pending)} valori modificati)"
            menu["discard"] = "Scarta le modifiche"
        else:
            menu["save"] = "Chiudi"

        return self.async_show_menu(step_id="init", menu_options=menu)

    def _current(self) -> dict:
        """Configuration as it stands, with any unsaved edits laid over it."""
        return {**self.config_entry.data, **self.config_entry.options, **self._pending}

    async def _async_section(self, step_id: str, user_input=None):
        """Show one section, then return to the menu once it is submitted."""
        if user_input is not None:
            if step_id == "schedule":
                user_input = _normalise_schedule(user_input)
            if step_id == "control":
                error = validate_control(user_input)
                if error:
                    return self.async_show_form(
                        step_id=step_id,
                        data_schema=vol.Schema(
                            _BUILDERS[step_id]({**self._current(), **user_input})
                        ),
                        errors={"base": error},
                    )
            self._pending.update(user_input)
            _LOGGER.debug("evcontrol: sezione %s modificata, %d valori in attesa",
                          step_id, len(self._pending))
            return await self.async_step_init()

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(_BUILDERS[step_id](self._current())),
        )

    # ── Exit points ──────────────────────────────────────────────────────
    async def async_step_save(self, user_input=None):
        """Write everything at once.

        Home Assistant reloads the integration when options change, so batching the
        whole session into a single write means one reload however many sections
        were touched -- and the coordinator keeps its charging session state
        instead of restarting it once per edit.
        """
        existing = {**self.config_entry.data, **self.config_entry.options}
        merged = {**existing, **self._pending}
        _LOGGER.debug("evcontrol: salvate %d modifiche", len(self._pending))
        return self.async_create_entry(title="", data=merged)

    async def async_step_discard(self, user_input=None):
        self._pending = {}
        return await self.async_step_init()

    # ── Section handlers ─────────────────────────────────────────────────
    async def async_step_inverter(self, user_input=None):
        return await self._async_section("inverter", user_input)

    async def async_step_consumption(self, user_input=None):
        return await self._async_section("consumption", user_input)

    async def async_step_battery(self, user_input=None):
        return await self._async_section("battery", user_input)

    async def async_step_charger(self, user_input=None):
        return await self._async_section("charger", user_input)

    async def async_step_ev_vehicle(self, user_input=None):
        return await self._async_section("ev_vehicle", user_input)

    async def async_step_schedule(self, user_input=None):
        return await self._async_section("schedule", user_input)

    async def async_step_auto(self, user_input=None):
        return await self._async_section("auto", user_input)

    async def async_step_smart(self, user_input=None):
        return await self._async_section("smart", user_input)

    async def async_step_smart_forecast(self, user_input=None):
        return await self._async_section("smart_forecast", user_input)

    async def async_step_control(self, user_input=None):
        return await self._async_section("control", user_input)
