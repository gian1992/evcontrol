"""Select platform for EV Control."""
import logging
from typing import Any

from homeassistant.components.select import SelectEntity
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
    """Set up select platform."""
    coordinator = hass.data.get("evcontrol", {}).get(config_entry.entry_id)
    
    if not coordinator:
        _LOGGER.error("Coordinator not found in hass.data")
        return

    async_add_entities([EVControlModeSelect(coordinator, config_entry)])


class EVControlModeSelect(SelectEntity):
    """Select entity for charging mode."""

    _attr_name = "Charging Mode"
    _attr_options = ["Eco", "Normal", "Power", "Auto", "Auto-Eco"]
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: EVControlCoordinator, config_entry: ConfigEntry) -> None:
        """Initialize the select."""
        self.coordinator = coordinator
        self.config_entry = config_entry
        self._attr_unique_id = f"evcontrol_{config_entry.entry_id}_mode"

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

    @property
    def current_option(self) -> str:
        """Return the current selected option."""
        if not self.coordinator.data:
            return "Eco"  # Default fallback
        mode = self.coordinator.data.get("mode", "eco")
        if not mode:
            return "Eco"
        # Ensure the mode is properly capitalized
        mode_lower = mode.lower()
        if mode_lower == "eco":
            return "Eco"
        elif mode_lower == "normal":
            return "Normal"
        elif mode_lower == "power":
            return "Power"
        elif mode_lower == "auto":
            return "Auto"
        elif mode_lower == "auto-eco":
            return "Auto-Eco"
        return "Eco"  # Final fallback

    async def async_select_option(self, option: str) -> None:
        """Set the selected option."""
        mode_lower = option.lower()
        if mode_lower in ["eco", "normal", "power", "auto", "auto-eco"]:
            self.coordinator.set_mode(mode_lower)
            _LOGGER.info(f"Switched to {option} mode")
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Subscribe to coordinator updates."""
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )
        await super().async_added_to_hass()
