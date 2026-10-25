"""Switch platform for EV Control.

Only the charging on/off switch remains. The five per-mode switches duplicated the
Charging Mode select -- and worse, they were toggles for a choice that is exclusive,
so the UI could show two modes on at once. Smart Solar Protection is gone too: what
it used to opt into is now the planner's core behaviour, not an option.
"""
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchDeviceClass
from homeassistant.config_entries import ConfigEntry
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
    """Set up switch platform."""
    coordinator = hass.data.get("evcontrol", {}).get(config_entry.entry_id)
    
    if not coordinator:
        _LOGGER.error("Coordinator not found in hass.data")
        return

    switches = [
        EVControlChargingSwitch(coordinator, config_entry),
    ]

    async_add_entities(switches)


class EVControlBaseSwitch(SwitchEntity):
    """Base class for EV Control switches."""

    def __init__(self, coordinator: EVControlCoordinator, config_entry: ConfigEntry) -> None:
        """Initialize the switch."""
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


class EVControlChargingSwitch(EVControlBaseSwitch):
    """Switch to enable/disable EV charging."""

    _attr_unique_id_suffix = "charging_enabled"
    _attr_name = "Charging Enabled"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    @property
    def is_on(self) -> bool:
        """Return True if charging is enabled."""
        return self.coordinator.data.get("charging_enabled", True)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable charging."""
        self.coordinator.set_charging_enabled(True)
        _LOGGER.info("Charging enabled")
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable charging."""
        self.coordinator.set_charging_enabled(False)
        _LOGGER.info("Charging disabled")
        await self.coordinator.stop_charging()
        self.async_write_ha_state()


