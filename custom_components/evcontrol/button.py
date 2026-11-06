"""Button platform for EV Control."""
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
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
    """Set up button platform."""
    coordinator = hass.data.get("evcontrol", {}).get(config_entry.entry_id)
    
    if not coordinator:
        _LOGGER.error("Coordinator not found in hass.data")
        return

    buttons = [
        EVControlStartButton(coordinator, config_entry),
        EVControlStopButton(coordinator, config_entry),
        EVControlUnblockButton(coordinator, config_entry),
    ]

    async_add_entities(buttons)


class EVControlBaseButton(ButtonEntity):
    """Base class for EV Control buttons."""

    def __init__(self, coordinator: EVControlCoordinator, config_entry: ConfigEntry) -> None:
        """Initialize the button."""
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


class EVControlStartButton(EVControlBaseButton):
    """Button to start charging."""

    _attr_unique_id_suffix = "start_charging"
    _attr_name = "Start Charging"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    async def async_press(self) -> None:
        """Press the button."""
        await self.coordinator.start_charging()
        _LOGGER.info("Manual start charging button pressed")


class EVControlStopButton(EVControlBaseButton):
    """Button to stop charging."""

    _attr_unique_id_suffix = "stop_charging"
    _attr_name = "Stop Charging"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    async def async_press(self) -> None:
        """Press the button."""
        await self.coordinator.stop_charging()
        _LOGGER.info("Manual stop charging button pressed")


class EVControlUnblockButton(EVControlBaseButton):
    """Button to drop the planner's waiting timers.

    Not a start button: see `EVControlCoordinator.unblock_now`. This removes the
    minimum-off pause and the start confirmation, then lets the planner decide on
    the next cycle as it normally would.
    """

    _attr_unique_id_suffix = "unblock_now"
    _attr_name = "Unblock Now"
    _attr_icon = "mdi:timer-off-outline"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def unique_id(self) -> str:
        """Return unique ID."""
        return f"{self._attr_unique_id}_{self._attr_unique_id_suffix}"

    async def async_press(self) -> None:
        """Press the button."""
        self.coordinator.unblock_now()
        await self.coordinator.async_request_refresh()
