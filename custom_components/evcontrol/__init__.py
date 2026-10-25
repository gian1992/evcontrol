"""EV Control component for Home Assistant."""
import logging
import re
from datetime import datetime, time as dt_time
from pathlib import Path
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.const import Platform
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.exceptions import ServiceValidationError

from .coordinator import EVControlCoordinator, SCHEDULE_DAYS

_LOGGER = logging.getLogger(__name__)

DOMAIN = "evcontrol"
PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.BUTTON, Platform.SELECT]

# Day groups accepted by the schedule services, so a card or an automation can say
# "weekdays" instead of sending the same call five times.
_DAY_GROUPS = {
    "all": SCHEDULE_DAYS,
    "weekdays": SCHEDULE_DAYS[:5],
    "weekend": SCHEDULE_DAYS[5:],
}

# Hours and minutes are matched loosely and range-checked below, so a hand-written
# "7:30" is accepted and padded rather than rejected on a missing zero.
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _resolve_days(raw) -> list[str]:
    """Turn the service's `day` field into a list of weekday keys.

    Accepts a single day, a list of days, one of the groups above, or "today" --
    which is what a dashboard button ends up sending most of the time.
    """
    if raw is None:
        raise ServiceValidationError("The 'day' field is required")

    values = raw if isinstance(raw, (list, tuple)) else [raw]
    days: list[str] = []
    for value in values:
        key = str(value).strip().lower()
        if key == "today":
            days.append(SCHEDULE_DAYS[datetime.now().weekday()])
        elif key in _DAY_GROUPS:
            days.extend(_DAY_GROUPS[key])
        elif key in SCHEDULE_DAYS:
            days.append(key)
        else:
            raise ServiceValidationError(
                f"Unknown day '{value}'. Expected one of: "
                + ", ".join(SCHEDULE_DAYS + ["today", *_DAY_GROUPS])
            )

    # Dedupe while keeping weekday order, so overlapping groups are harmless.
    return [d for d in SCHEDULE_DAYS if d in set(days)]


def _normalise_time(raw) -> str:
    """Return "HH:MM", or raise if it is not a time.

    HA's time selector hands over a `datetime.time` or an "HH:MM:SS" string
    depending on where the call came from; the planner parses "HH:MM".
    """
    if isinstance(raw, dt_time):
        return raw.strftime("%H:%M")
    text = str(raw or "").strip()
    if not text:
        raise ServiceValidationError("The 'time' field is required")
    # Drop seconds when the selector sends them.
    if text.count(":") == 2:
        text = text.rsplit(":", 1)[0]
    match = _TIME_RE.match(text)
    if not match:
        raise ServiceValidationError(f"Invalid time '{raw}'. Expected HH:MM, e.g. 07:30")
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        raise ServiceValidationError(f"Invalid time '{raw}'. Expected HH:MM, e.g. 07:30")
    return f"{hour:02d}:{minute:02d}"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the Lovelace card as a static resource (runs once at startup)."""
    www_path = Path(__file__).parent / "www" / "evcontrol-card.js"
    url = "/evcontrol/evcontrol-card.js"
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(url, str(www_path), cache_headers=False)]
        )
        add_extra_js_url(hass, url)
        _LOGGER.info("evcontrol-card.js registered as frontend resource at %s", url)
    except Exception as e:
        _LOGGER.warning("Could not register evcontrol-card.js: %s", e)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EV Control from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create coordinator once and share with all platforms
    coordinator = EVControlCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Schedule edits from the card update the config entry but apply in place, so the
    # update listener has to skip the reload for those -- one per queued update.
    skip_reload = {"pending": 0}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    # Register services
    async def handle_set_mode(call: ServiceCall) -> None:
        """Handle set_mode service."""
        mode = call.data.get("mode", "eco").lower()
        coordinator.set_mode(mode)
        await coordinator.async_request_refresh()
        _LOGGER.info(f"Mode changed to: {mode}")
    
    async def handle_start_charging(call: ServiceCall) -> None:
        """Handle start_charging service."""
        coordinator.set_charging_enabled(True)
        await coordinator.async_request_refresh()
        _LOGGER.info("Charging started")
    
    async def handle_stop_charging(call: ServiceCall) -> None:
        """Handle stop_charging service."""
        coordinator.set_charging_enabled(False)
        await coordinator.async_request_refresh()
        _LOGGER.info("Charging stopped")
    
    async def handle_set_speed(call: ServiceCall) -> None:
        """Handle set_speed service."""
        speed = call.data.get("speed", 15)
        coordinator.set_base_current(speed)
        await coordinator.async_request_refresh()
        _LOGGER.info(f"Charging speed set to: {speed}A")
    
    async def _async_write_schedule(updates: dict) -> None:
        """Persist schedule changes to the config entry, then apply them in place.

        Writing the options is what makes the change survive a restart and show up in
        the options flow. The in-place apply is what keeps the running session intact:
        see `EVControlCoordinator.apply_target_schedule`. The two have to agree, hence
        one path for both.
        """
        options = {
            **entry.options,
            **{f"target_schedule_{day}": value or "" for day, value in updates.items()},
        }
        # The update listener below reloads the entry on any config-entry change. This
        # one does not need a reload -- the coordinator is patched directly -- so the
        # listener is told to let exactly this update through untouched.
        skip_reload["pending"] += 1
        if not hass.config_entries.async_update_entry(entry, options=options):
            # Nothing changed, so no listener call is coming to consume the token.
            # Leaving it queued would swallow the next real options-flow reload.
            skip_reload["pending"] -= 1
        coordinator.apply_target_schedule(updates)
        await coordinator.async_request_refresh()

    async def handle_set_schedule(call: ServiceCall) -> None:
        """Set the charging deadline for one or more days."""
        days = _resolve_days(call.data.get("day"))
        time_str = _normalise_time(call.data.get("time"))
        await _async_write_schedule({day: time_str for day in days})
        _LOGGER.info("Schedule set to %s for %s", time_str, ", ".join(days))

    async def handle_clear_schedule(call: ServiceCall) -> None:
        """Remove the charging deadline from one or more days."""
        days = _resolve_days(call.data.get("day"))
        await _async_write_schedule({day: "" for day in days})
        _LOGGER.info("Schedule cleared for %s", ", ".join(days))

    async def handle_set_start_time(call: ServiceCall) -> None:
        """Handle set_start_time service.

        There is no configurable start time: in AUTO the planner derives it from the
        deadline, the charge still needed and the solar forecast. Kept registered so
        existing automations do not break, but it says so rather than failing silently.
        """
        _LOGGER.warning(
            "evcontrol.set_start_time does nothing: the start time is computed from "
            "the deadline. Use evcontrol.set_schedule to move the deadline instead."
        )

    async def handle_set_end_time(call: ServiceCall) -> None:
        """Deprecated alias of set_schedule.

        Also accepts the shape the bundled Lovelace card sends -- an `end_time` and no
        day at all, from a single time field with no day picker. That control used to
        be a silent no-op; defaulting it to today is what it visibly means.
        """
        day = call.data.get("day") or "today"
        time_str = _normalise_time(call.data.get("time") or call.data.get("end_time"))
        await _async_write_schedule({d: time_str for d in _resolve_days(day)})
        _LOGGER.info("Schedule set to %s for %s (via set_end_time)", time_str, day)
    
    # Register all services
    hass.services.async_register(DOMAIN, "set_mode", handle_set_mode)
    hass.services.async_register(DOMAIN, "start_charging", handle_start_charging)
    hass.services.async_register(DOMAIN, "stop_charging", handle_stop_charging)
    hass.services.async_register(DOMAIN, "set_speed", handle_set_speed)
    hass.services.async_register(DOMAIN, "set_schedule", handle_set_schedule)
    hass.services.async_register(DOMAIN, "clear_schedule", handle_clear_schedule)
    hass.services.async_register(DOMAIN, "set_start_time", handle_set_start_time)
    hass.services.async_register(DOMAIN, "set_end_time", handle_set_end_time)
    
    # Reload coordinator when options are updated via the options flow
    async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
        if skip_reload["pending"] > 0:
            skip_reload["pending"] -= 1
            return
        await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
