"""Number platform for EV Control."""
import logging
from typing import Any

from homeassistant.components.number import (
    NumberEntity,
    NumberMode,
    RestoreNumber,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfElectricCurrent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory

from .coordinator import EVControlCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up number platform."""
    coordinator = hass.data.get("evcontrol", {}).get(config_entry.entry_id)
    
    if not coordinator:
        _LOGGER.error("Coordinator not found in hass.data")
        return

    numbers = [
        EVControlGridPowerNumber(coordinator, config_entry),
        EVControlPhase1SOCThresholdNumber(coordinator, config_entry),
        EVControlNormalModeCurrentNumber(coordinator, config_entry),
        EVControlBatteryReservationNumber(coordinator, config_entry),
        EVControlSmartBatteryFloorNumber(coordinator, config_entry),
        EVControlSolarHoldStopNumber(coordinator, config_entry),
        EVControlSolarHoldSocNumber(coordinator, config_entry),
    ]

    async_add_entities(numbers)


class EVControlBaseNumber(RestoreNumber):
    """Base class for EV Control numbers."""

    def __init__(self, coordinator: EVControlCoordinator, config_entry: ConfigEntry) -> None:
        """Initialize the number."""
        self.coordinator = coordinator
        self.config_entry = config_entry
        self._attr_unique_id = f"evcontrol_{config_entry.entry_id}"
        self._attr_mode = NumberMode.SLIDER

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
        """Restore previous state and subscribe to updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )


class EVControlGridPowerNumber(EVControlBaseNumber):
    """Number entity for grid power limit (3-12 kW)."""

    _attr_unique_id_suffix = "grid_power"
    _attr_name = "Grid Power Limit"
    _attr_native_min_value = 3.0
    _attr_native_max_value = 12.0
    _attr_native_step = 0.5
    _attr_native_unit_of_measurement = UnitOfPower.KILO_WATT
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self.coordinator.data.get("grid_power_limit", 12.0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the grid power limit."""
        self.coordinator.set_grid_power_limit(value)
        _LOGGER.info(f"Grid power limit set to {value} kW")
        self.async_write_ha_state()


class EVControlPhase1SOCThresholdNumber(EVControlBaseNumber):
    """Number entity for AUTO Phase 1 SOC threshold."""

    _attr_unique_id_suffix = "auto_phase1_soc_threshold"
    _attr_name = "AUTO Phase 1 SOC Threshold"
    _attr_native_min_value = 10.0
    _attr_native_max_value = 80.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self.config_entry.data.get("auto_phase1_soc_threshold", 50.0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the AUTO Phase 1 SOC threshold."""
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                **self.config_entry.data,
                "auto_phase1_soc_threshold": float(value),
            },
        )
        _LOGGER.info(f"AUTO Phase 1 SOC threshold set to {value}%")
        await self.hass.config_entries.async_reload(self.config_entry.entry_id)
        self.async_write_ha_state()


class EVControlNormalModeCurrentNumber(EVControlBaseNumber):
    """Number entity for normal mode base current."""

    _attr_unique_id_suffix = "normal_current"
    _attr_name = "Normal Mode Base Current"
    _attr_native_min_value = 6.0
    _attr_native_max_value = 32.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self.coordinator.data.get("base_current", 6.0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the base current for normal mode."""
        self.coordinator.set_base_current(value)
        _LOGGER.info(f"Normal mode base current set to {value} A")
        self.async_write_ha_state()


class EVControlBatteryReservationNumber(EVControlBaseNumber):
    """Number entity for battery reservation power in Normal mode."""

    _attr_unique_id_suffix = "battery_reservation"
    _attr_name = "Normal Mode Battery Reservation"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 2000.0
    _attr_native_step = 100.0
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        """Return the current value."""
        return self.coordinator.data.get("battery_reservation_power", 500.0)

    async def async_set_native_value(self, value: float) -> None:
        """Set the battery reservation power."""
        self.coordinator.set_battery_reservation(value)
        _LOGGER.info(f"Battery reservation power set to {value} W")
        self.async_write_ha_state()


class EVControlSmartBatteryFloorNumber(EVControlBaseNumber):
    """Power the home battery is always guaranteed, in the smart planner.

    This is the main day-to-day control, and it replaces the fixed reservation
    concept entirely. The old reservation tried to hold back whatever the battery
    *might* absorb -- a quantity that swings between roughly 1 kW when the pack is
    warm and 2.5 kW on a cool morning, so no single number was ever right.

    A floor is different: it is the slowest charge rate you are willing to accept
    for the house battery so that the car can run at all. Everything the battery
    can absorb above this floor is still hers whenever the planner is in a
    protective mood; everything above it that she cannot take goes to the car
    rather than to the grid.

    The effective start threshold for the car is therefore
    (start current x voltage) + this value.
    """

    _attr_unique_id_suffix = "smart_batt_floor"
    _attr_name = "Smart Battery Floor"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 3000.0
    _attr_native_step = 100.0
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.get(
            "smart_batt_floor_w",
            self.coordinator.config.get("smart_batt_floor_w", 500.0),
        )

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_smart_value("smart_batt_floor_w", value)
        self.async_write_ha_state()


class EVControlSolarHoldStopNumber(EVControlBaseNumber):
    """PV production below which the day is considered over and charging stops.

    Set it low. Charging starts on surplus but is held on *production*, and the
    huge gap between those two conditions is the entire anti-cycling mechanism:
    once a session opens it normally runs until sunset, whatever the house does in
    between.
    """

    _attr_unique_id_suffix = "solar_hold_stop"
    _attr_name = "Solar Stop Threshold"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 3000.0
    _attr_native_step = 50.0
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.get(
            "solar_stop_pv_w",
            self.coordinator.config.get("solar_stop_pv_w", 400.0),
        )

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_smart_value("solar_stop_pv_w", value)
        self.async_write_ha_state()


class EVControlSolarHoldSocNumber(EVControlBaseNumber):
    """Home battery SOC below which the session ends.

    The only other thing that stops a session. Above it the charger holds its
    minimum and the house battery covers whatever the sun does not, which is the
    trade this design makes on purpose.
    """

    _attr_unique_id_suffix = "solar_hold_soc"
    _attr_name = "Home Battery Min SOC"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_native_unit_of_measurement = "%"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.get(
            "solar_min_soc",
            self.coordinator.config.get("solar_min_soc", 60.0),
        )

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.set_smart_value("solar_min_soc", value)
        self.async_write_ha_state()
