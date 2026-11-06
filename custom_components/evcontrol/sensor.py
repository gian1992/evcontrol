"""Sensor platform for EV Control.

Deliberately small. Entities that only echoed a configuration value back
(Available Battery Power, Grid Power Limit), duplicated a control (Charging Mode,
which the select entity already owns), or belonged to the superseded ECO timers
were removed: a long list of readings is not the same as being able to tell what
the charger is doing.
"""
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorStateClass,
    SensorDeviceClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfElectricCurrent, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .coordinator import EVControlCoordinator

_LOGGER = logging.getLogger(__name__)


def _as_utc(value):
    """Make a naive local datetime tz-aware, as timestamp sensors require."""
    if value is None:
        return None
    return dt_util.as_utc(value) if value.tzinfo is None else value


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor platform."""
    coordinator = hass.data.get("evcontrol", {}).get(config_entry.entry_id)
    
    if not coordinator:
        _LOGGER.error("Coordinator not found in hass.data")
        return

    sensors = [
        EVControlAvailableCurrentSensor(coordinator, config_entry),
        EVControlSurplusSensor(coordinator, config_entry),
        EVControlPVPowerSensor(coordinator, config_entry),
        EVControlHouseConsumptionSensor(coordinator, config_entry),
        EVControlEVPowerSensor(coordinator, config_entry),
        EVControlBatterySOCSensor(coordinator, config_entry),
        EVControlEVSOCSensor(coordinator, config_entry),
        EVControlDebugSensor(coordinator, config_entry),
        EVControlOutlookSensor(coordinator, config_entry),
        EVControlSmartStateSensor(coordinator, config_entry),
        EVControlSmartHeadroomSensor(coordinator, config_entry),
        EVControlSmartReserveSensor(coordinator, config_entry),
        EVControlSmartGridSensor(coordinator, config_entry),
        EVControlSmartStopsSensor(coordinator, config_entry),
        EVControlNextTargetSensor(coordinator, config_entry),
        EVControlCriticalAtSensor(coordinator, config_entry),
        EVControlChargeTimeSensor(coordinator, config_entry),
        EVControlTimeToFullSensor(coordinator, config_entry),
    ]

    async_add_entities(sensors)


class EVControlBaseSensor(SensorEntity):
    """Base class for EV Control sensors."""

    def __init__(self, coordinator: EVControlCoordinator, config_entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        self.coordinator = coordinator
        self.config_entry = config_entry
        self._attr_unique_id = f"evcontrol_{config_entry.entry_id}"

    @property
    def device_info(self) -> dict:
        """Return device info."""
        return {
            "identifiers": {("evcontrol", self.config_entry.entry_id)},
            "name": "EV Control",
            "manufacturer": "EV Control",
        }

    @property
    def should_poll(self) -> bool:
        """No polling needed."""
        return False

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
        await super().async_added_to_hass()


class EVControlSurplusSensor(EVControlBaseSensor):
    """Sensor for surplus power."""

    _attr_unique_id_suffix = "surplus"
    _attr_name = "Surplus Power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        """Return the state."""
        return self.coordinator.data.get("surplus", 0)


class EVControlAvailableCurrentSensor(EVControlBaseSensor):
    """Sensor for available charging current."""

    _attr_unique_id_suffix = "available_current"
    _attr_name = "Available Charging Current"
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        """Return the state."""
        return self.coordinator.data.get("available_current", 0)


class EVControlPVPowerSensor(EVControlBaseSensor):
    """Sensor for PV power generation."""

    _attr_unique_id_suffix = "pv_power"
    _attr_name = "PV Power Generation"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        """Return the state."""
        return self.coordinator.data.get("pv_power", 0)


class EVControlHouseConsumptionSensor(EVControlBaseSensor):
    """Sensor for house power consumption."""

    _attr_unique_id_suffix = "house_consumption"
    _attr_name = "House Power Consumption"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        """Return the state."""
        return self.coordinator.data.get("house_consumption", 0)


class EVControlEVPowerSensor(EVControlBaseSensor):
    """Sensor for EV charger power consumption."""

    _attr_unique_id_suffix = "ev_power"
    _attr_name = "EV Power Consumption"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        """Return the state."""
        return self.coordinator.data.get("ev_power", 0)


class EVControlBatterySOCSensor(EVControlBaseSensor):
    """Sensor for battery state of charge."""

    _attr_unique_id_suffix = "battery_soc"
    _attr_name = "Battery State of Charge"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        """Return the state."""
        return self.coordinator.data.get("battery_soc", 0)


class EVControlEVSOCSensor(EVControlBaseSensor):
    """Sensor for EV battery state of charge."""

    _attr_unique_id_suffix = "ev_soc"
    _attr_name = "EV State of Charge"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        """Return the state."""
        return self.coordinator.data.get("ev_soc", 0)


class EVControlDebugSensor(EVControlBaseSensor):
    """What the charger is doing right now, in one sentence.

    Not a diagnostic entity: this is the one to put on the dashboard. The state is
    the action ("Carica a 10 A"), the `perche` attribute is the reason, and the
    technical planner text stays available under `dettaglio_tecnico` for when
    something actually needs debugging.

    The unique_id suffix stays "debug" so existing dashboards and history keep
    working across this change.
    """

    _attr_unique_id_suffix = "debug"
    _attr_name = "Charging Status"
    _attr_icon = "mdi:ev-station"

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> str:
        """Return the state. Home Assistant caps sensor states at 255 characters."""
        headline = self.coordinator.data.get("status_headline") or             self.coordinator.data.get("status_message", "")
        return headline[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Plain-language keys first, then the raw data.

        The raw dump has to stay: the Lovelace card reads pv_power, surplus,
        available_current and the rest from this entity. What changed is that the
        answer -- what it is doing and why -- is no longer buried inside it.
        """
        data = dict(self.coordinator.data)
        nested = data.pop("status_attributes", None) or {}
        return {**nested, **data}


class EVControlOutlookSensor(EVControlBaseSensor):
    """Will the car be charged in time?

    The state is the verdict; `al_piu_tardi_rete_dalle` is the number that makes it
    possible to stop watching: the latest moment at which falling back to the grid
    still gets the car full by the deadline. If the sun never arrives, that is when
    the grid takes over -- and the car is ready anyway.
    """

    _attr_unique_id_suffix = "outlook"
    _attr_name = "Charging Outlook"
    _attr_icon = "mdi:calendar-clock"

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> str:
        return (self.coordinator.data.get("outlook") or "")[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        attrs = {
            "spiegazione": d.get("outlook_detail"),
            "al_piu_tardi_rete_dalle": d.get("grid_fallback_at"),
            "piena_alle": d.get("full_at"),
            "soc_auto": d.get("ev_soc"),
            "target": (d.get("status_attributes") or {}).get("target"),
            "tempo_disponibile": (d.get("status_attributes") or {}).get("tempo_disponibile"),
            "tempo_necessario": (d.get("status_attributes") or {}).get("tempo_necessario"),
            "energia_mancante_kwh": (d.get("status_attributes") or {}).get("energia_mancante_kwh"),
        }
        return {k: v for k, v in attrs.items() if v is not None}


class EVControlSmartStateSensor(EVControlBaseSensor):
    """Planner state, with the full decision context as attributes."""

    _attr_unique_id_suffix = "smart_state"
    _attr_name = "Smart Charging State"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> str:
        return self.coordinator.data.get("smart_state") or "off"

    @property
    def extra_state_attributes(self) -> dict:
        d = self.coordinator.data
        return {
            "urgency": d.get("smart_urgency"),
            "slack": d.get("smart_slack"),
            "pv_trend": d.get("smart_trend"),
            "battery_share_factor": d.get("smart_share_factor"),
            "battery_capability_w": d.get("smart_capability_w"),
            "projected_battery_soc": d.get("smart_projected_batt_soc"),
            "dwell_left_s": d.get("smart_dwell_left_s"),
            "house_avg_w": d.get("house_avg_w"),
            "reason": d.get("debug_message"),
        }


class EVControlSmartHeadroomSensor(EVControlBaseSensor):
    """Power the EV is allowed to take right now, after the battery's share."""

    _attr_unique_id_suffix = "smart_headroom"
    _attr_name = "Smart Headroom"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self):
        return self.coordinator.data.get("smart_headroom_w")


class EVControlSmartReserveSensor(EVControlBaseSensor):
    """Power currently reserved for the home battery. Varies every cycle."""

    _attr_unique_id_suffix = "smart_reserve"
    _attr_name = "Smart Battery Reserve"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self):
        return self.coordinator.data.get("smart_reserve_w")


class EVControlSmartGridSensor(EVControlBaseSensor):
    """Grid exchange derived from the energy balance. Positive = importing."""

    _attr_unique_id_suffix = "smart_grid"
    _attr_name = "Smart Grid Exchange"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self):
        return self.coordinator.data.get("smart_grid_w")


class EVControlSmartStopsSensor(EVControlBaseSensor):
    """Stop count for today, against the daily budget."""

    _attr_unique_id_suffix = "smart_stops"
    _attr_name = "Smart Stops Today"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self):
        return self.coordinator.data.get("smart_stops_today")


# ── Deadline and duration diagnostics ────────────────────────────────────────
# Deliberately four separate sensors rather than attributes on one: these are the
# numbers you want to read at a glance in the diagnostics card, and an attribute
# is one click away from being invisible. All four are computed in every mode --
# "when does this get critical" is exactly the question you ask while sitting in
# NORMAL or POWER.


class EVControlNextTargetSensor(EVControlBaseSensor):
    """When the car is next due, and how full, from the weekly schedule."""

    _attr_unique_id_suffix = "next_target"
    _attr_name = "Next Charge Target"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-clock"

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self):
        return _as_utc(self.coordinator.data.get("next_target_at"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data.get("debug_attributes") or {}
        attrs = {k: d[k] for k in ("prossimo_target", "fra") if k in d}
        # The percentage that deadline asks for: without it the timestamp
        # alone cannot tell you whether 07:00 means a full car or an 80% one.
        target_soc = self.coordinator.data.get("auto_target_soc")
        if target_soc is not None:
            attrs["target_soc"] = round(target_soc)
        schedule = self.coordinator.data.get("target_soc_schedule")
        if schedule:
            attrs["target_soc_schedule"] = dict(schedule)
        return attrs


class EVControlCriticalAtSensor(EVControlBaseSensor):
    """When waiting for sun stops and the grid takes over.

    Target time minus the time a full charge needs at the reference current, plus
    its safety margin. Before this moment the controller is free to hold out for
    the sun; after it, missing the deadline becomes the bigger risk.
    """

    _attr_unique_id_suffix = "critical_at"
    _attr_name = "Grid Fallback At"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:transmission-tower-import"

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self):
        return _as_utc(self.coordinator.data.get("critical_at"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data.get("debug_attributes") or {}
        return {k: d[k] for k in ("critical_alle", "critical_fra") if k in d}


class EVControlChargeTimeSensor(EVControlBaseSensor):
    """How long a full charge takes at the configured reference current."""

    _attr_unique_id_suffix = "charge_time_ref"
    _attr_name = "Full Charge Duration"
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:timer-sand"

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self):
        hours = self.coordinator.data.get("charge_hours_at_reference")
        return round(hours, 2) if hours is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data.get("debug_attributes") or {}
        keys = [k for k in d if k.startswith("carica_a_")]
        out = {k: d[k] for k in keys}
        for k in ("soc_auto", "energia_mancante_kwh"):
            if k in d:
                out[k] = d[k]
        return out


class EVControlTimeToFullSensor(EVControlBaseSensor):
    """How long to 100% at whatever the car is drawing right now.

    Unknown when the car is not charging: dividing the energy still needed by a
    near-zero power is how the old estimate produced answers like 285 hours.
    """

    _attr_unique_id_suffix = "time_to_full"
    _attr_name = "Time To Full"
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:battery-clock"

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self):
        hours = self.coordinator.data.get("hours_to_full_now")
        return round(hours, 2) if hours is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data.get("debug_attributes") or {}
        return {k: d[k] for k in ("al_ritmo_attuale", "piena_alle", "potenza_auto_w")
                if k in d}
