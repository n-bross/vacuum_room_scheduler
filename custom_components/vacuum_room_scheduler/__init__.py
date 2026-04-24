"""Vacuum Room Scheduler custom integration."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, time
import logging
import re
from typing import Any

from homeassistant.components import vacuum
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_STATE_CHANGED,
    STATE_HOME,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_point_in_time, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util
import voluptuous as vol

from .const import (
    ATTR_ENTRY_ID,
    ATTR_MODE,
    ATTR_RESPONSE,
    ATTR_ROOM,
    CHECK_INTERVAL_MINUTES,
    CONF_MAX_DAYS,
    CONF_MEDIA_PLAYER_ENTITY_ID,
    CONF_PRESENCE_ENTITY_ID,
    CONF_ROOM_NAME,
    CONF_ROOMS,
    CONF_SEGMENT_ID,
    CONF_TTS_SERVICE,
    CONF_VACUUM_ENTITY_ID,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DATA_MANAGERS,
    DATA_SERVICES_REGISTERED,
    DEFAULT_MAX_DAYS,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DOMAIN,
    EVENT_RESPONSE,
    PROMPT_COOLDOWN_HOURS,
    REMINDER_MINUTES,
    SERVICE_HANDLE_RESPONSE,
    STORAGE_VERSION,
)
from .room_discovery import (
    discover_floor_area_names,
    discover_rooms_on_same_floor,
    filter_rooms_by_allowed_names,
)

_LOGGER = logging.getLogger(__name__)

NOW_WORDS = {"jetzt", "now", "sofort", "immediately"}
HOURS_RE = re.compile(r"\bin\s*(\d+)\s*(stunden?|hours?|h)\b", re.IGNORECASE)
DAYS_RE = re.compile(r"\bin\s*(\d+)\s*(tagen?|tage?|days?|d)\b", re.IGNORECASE)
TOKEN_RE = re.compile(r"\w+")

CLEAN_MODE_VACUUM = "vacuum"
CLEAN_MODE_MOP = "mop"
CLEAN_MODES = (CLEAN_MODE_VACUUM, CLEAN_MODE_MOP)

MODE_ALIASES = {
    CLEAN_MODE_VACUUM: CLEAN_MODE_VACUUM,
    "saugen": CLEAN_MODE_VACUUM,
    "saug": CLEAN_MODE_VACUUM,
    "vacuuming": CLEAN_MODE_VACUUM,
    CLEAN_MODE_MOP: CLEAN_MODE_MOP,
    "wischen": CLEAN_MODE_MOP,
    "wisch": CLEAN_MODE_MOP,
    "mopping": CLEAN_MODE_MOP,
}

MODE_SPEECH_LABELS = {
    CLEAN_MODE_VACUUM: "vacuuming",
    CLEAN_MODE_MOP: "mopping",
}

MODE_PAST_PARTICIPLES = {
    CLEAN_MODE_VACUUM: "vacuumed",
    CLEAN_MODE_MOP: "mopped",
}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Vacuum Room Scheduler integration."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(DATA_MANAGERS, {})

    if not hass.data[DOMAIN].get(DATA_SERVICES_REGISTERED):
        _register_services(hass)
        hass.data[DOMAIN][DATA_SERVICES_REGISTERED] = True

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Vacuum Room Scheduler from a config entry."""
    manager = VacuumRoomSchedulerManager(hass, entry)
    await manager.async_start()

    hass.data[DOMAIN][DATA_MANAGERS][entry.entry_id] = manager
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    manager: VacuumRoomSchedulerManager | None = hass.data[DOMAIN][DATA_MANAGERS].pop(
        entry.entry_id, None
    )
    if manager is not None:
        await manager.async_stop()

    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry after updates."""
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)


def _register_services(hass: HomeAssistant) -> None:
    """Register domain services."""

    async def _async_handle_response(call: ServiceCall) -> None:
        response = call.data[ATTR_RESPONSE]
        room = call.data.get(ATTR_ROOM)
        mode = call.data.get(ATTR_MODE)
        entry_id = call.data.get(ATTR_ENTRY_ID)

        managers = _select_managers(hass, entry_id)
        if not managers:
            raise HomeAssistantError("No matching Vacuum Room Scheduler entry found")

        for manager in managers:
            await manager.async_handle_response(response=response, room=room, mode=mode)

    hass.services.async_register(
        DOMAIN,
        SERVICE_HANDLE_RESPONSE,
        _async_handle_response,
        schema=vol.Schema(
            {
                vol.Required(ATTR_RESPONSE): cv.string,
                vol.Optional(ATTR_ROOM): cv.string,
                vol.Optional(ATTR_MODE): cv.string,
                vol.Optional(ATTR_ENTRY_ID): cv.string,
            }
        ),
    )


def _select_managers(
    hass: HomeAssistant, entry_id: str | None
) -> list["VacuumRoomSchedulerManager"]:
    managers: dict[str, VacuumRoomSchedulerManager] = hass.data[DOMAIN][DATA_MANAGERS]

    if entry_id:
        manager = managers.get(entry_id)
        return [manager] if manager is not None else []

    return list(managers.values())


class VacuumRoomSchedulerManager:
    """Runtime manager for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize runtime state."""
        self.hass = hass
        self.entry = entry
        self.config = {**entry.data, **entry.options}

        self.vacuum_entity_id: str = self.config[CONF_VACUUM_ENTITY_ID]
        self.presence_entity_id: str = self.config[CONF_PRESENCE_ENTITY_ID]
        self.tts_service: str = self.config[CONF_TTS_SERVICE]
        self.media_player_entity_id: str = self.config[CONF_MEDIA_PLAYER_ENTITY_ID]
        self.max_days: int = int(self.config.get(CONF_MAX_DAYS, DEFAULT_MAX_DAYS))

        self.window_start: time = _parse_time(
            self.config.get(CONF_WINDOW_START), DEFAULT_WINDOW_START
        )
        self.window_end: time = _parse_time(
            self.config.get(CONF_WINDOW_END), DEFAULT_WINDOW_END
        )

        self._configured_rooms: dict[str, int] = _normalize_rooms(
            self.config.get(CONF_ROOMS, [])
        )
        self.rooms: dict[str, int] = {}

        self.store = Store[dict[str, Any]](
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}_{entry.entry_id}",
        )

        self._last_cleaned: dict[str, str] = {}
        self._last_prompted: dict[str, str] = {}
        self._scheduled: dict[str, str] = {}

        self._scheduled_unsubs: dict[str, list[Callable[[], None]]] = {}
        self._unsub_listeners: list[Callable[[], None]] = []

    async def async_start(self) -> None:
        """Load state and start runtime listeners."""
        self._refresh_rooms()
        await self._async_load_state()

        for task_key, when_str in list(self._scheduled.items()):
            target = _target_from_task_key(task_key, self.rooms)
            when = _parse_datetime(when_str)
            if target is None or when is None:
                self._scheduled.pop(task_key, None)
                continue

            room, mode = target
            if when <= dt_util.now():
                self.hass.async_create_task(
                    self._async_handle_scheduled_start(room, mode)
                )
                continue

            self._set_scheduled_callbacks(room, mode, when)

        self._unsub_listeners.append(
            async_track_time_interval(
                self.hass,
                self._async_periodic_check,
                timedelta(minutes=CHECK_INTERVAL_MINUTES),
            )
        )

        self._unsub_listeners.append(
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._async_state_changed_listener)
        )
        self._unsub_listeners.append(
            self.hass.bus.async_listen(EVENT_RESPONSE, self._async_custom_response_listener)
        )

        await self._async_save_state()
        await self._async_periodic_check()

        _LOGGER.debug("Started Vacuum Room Scheduler for entry %s", self.entry.entry_id)

    def _refresh_rooms(self) -> None:
        """Build effective room set from config or HA discovery, filtered by floor."""
        floor_room_names, anchor_area_name = discover_floor_area_names(
            self.hass, self.vacuum_entity_id
        )

        if self._configured_rooms:
            if floor_room_names:
                self.rooms = filter_rooms_by_allowed_names(
                    self._configured_rooms, floor_room_names
                )
            else:
                self.rooms = dict(self._configured_rooms)

            if not self.rooms:
                _LOGGER.warning(
                    "No configured rooms matched the vacuum floor for entry %s "
                    "(vacuum area: %s).",
                    self.entry.entry_id,
                    anchor_area_name or "unknown",
                )
            return

        discovered_rooms, _ = discover_rooms_on_same_floor(
            self.hass, self.vacuum_entity_id
        )
        self.rooms = discovered_rooms

        if self.rooms:
            _LOGGER.info(
                "Auto-discovered %s rooms for entry %s: %s",
                len(self.rooms),
                self.entry.entry_id,
                ", ".join(sorted(self.rooms)),
            )
        else:
            _LOGGER.warning(
                "No rooms discovered from Home Assistant/vacuum for entry %s",
                self.entry.entry_id,
            )

    async def async_stop(self) -> None:
        """Stop runtime listeners and scheduled callbacks."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

        for unsubs in self._scheduled_unsubs.values():
            for unsub in unsubs:
                unsub()
        self._scheduled_unsubs.clear()

        await self._async_save_state()

        _LOGGER.debug("Stopped Vacuum Room Scheduler for entry %s", self.entry.entry_id)

    async def async_handle_response(
        self,
        response: str,
        room: str | None = None,
        mode: str | None = None,
    ) -> None:
        """Handle response text from service call, input_select, or intent event."""
        explicit_mode = _normalize_clean_mode(mode) if mode is not None else None
        if mode is not None and explicit_mode is None:
            _LOGGER.warning("Unsupported mode '%s' in response handler", mode)
            return

        requested_mode = explicit_mode or _parse_mode_from_text(response)
        target = self._resolve_target(room=room, mode=requested_mode)
        if target is None:
            _LOGGER.warning(
                "No target resolved for response '%s' in entry %s",
                response,
                self.entry.entry_id,
            )
            return

        target_room, target_mode = target
        decision = _parse_response_text(response)
        if decision is None:
            _LOGGER.warning("Could not parse response '%s'", response)
            await self._async_speak(
                f"I could not understand '{response}'. "
                "Please say 'jetzt', 'in 2 Stunden', or 'in 3 Tagen'."
            )
            return

        if decision == "now":
            _LOGGER.info(
                "Response for room %s (%s): clean now", target_room, target_mode
            )
            started = await self._async_start_room_clean(
                room=target_room,
                mode=target_mode,
                reason="voice_now",
            )
            if not started:
                # Keep intent intact by scheduling a near-term retry.
                retry_at = dt_util.now() + timedelta(minutes=10)
                await self._async_schedule_room(target_room, target_mode, retry_at)
                await self._async_speak(
                    f"Okay, I scheduled {target_room} for {_mode_speech(target_mode)} "
                    f"retry at {dt_util.as_local(retry_at).strftime('%H:%M')}."
                )
            return

        schedule_at = dt_util.now() + decision
        await self._async_schedule_room(target_room, target_mode, schedule_at)
        await self._async_speak(
            f"Okay, I scheduled {target_room} for {_mode_speech(target_mode)} at "
            f"{dt_util.as_local(schedule_at).strftime('%Y-%m-%d %H:%M')}."
        )

    async def _async_load_state(self) -> None:
        """Load persistent scheduler state."""
        data = await self.store.async_load() or {}

        self._last_cleaned = _normalize_task_state(
            data.get("last_cleaned_by_mode", {}), self.rooms
        )
        legacy_last_cleaned = data.get("last_cleaned", {})
        if isinstance(legacy_last_cleaned, dict):
            for room, value in legacy_last_cleaned.items():
                if room not in self.rooms or not isinstance(value, str):
                    continue
                for mode in CLEAN_MODES:
                    self._last_cleaned.setdefault(_task_key(room, mode), value)

        self._last_prompted = _normalize_task_state(
            data.get("last_prompted_by_mode", {}), self.rooms
        )
        legacy_last_prompted = data.get("last_prompted", {})
        if isinstance(legacy_last_prompted, dict):
            for room, value in legacy_last_prompted.items():
                if room not in self.rooms or not isinstance(value, str):
                    continue
                self._last_prompted.setdefault(
                    _task_key(room, CLEAN_MODE_VACUUM), value
                )

        self._scheduled = _normalize_task_state(
            data.get("scheduled_by_mode", {}), self.rooms
        )
        legacy_scheduled = data.get("scheduled", {})
        if isinstance(legacy_scheduled, dict):
            for room, value in legacy_scheduled.items():
                if room not in self.rooms or not isinstance(value, str):
                    continue
                self._scheduled.setdefault(_task_key(room, CLEAN_MODE_VACUUM), value)

    async def _async_save_state(self) -> None:
        """Persist scheduler state."""
        await self.store.async_save(
            {
                "last_cleaned_by_mode": self._last_cleaned,
                "last_prompted_by_mode": self._last_prompted,
                "scheduled_by_mode": self._scheduled,
            }
        )

    async def _async_periodic_check(self, _now: datetime | None = None) -> None:
        """Periodically evaluate all room/mode tasks."""
        now = dt_util.now()
        someone_home = self._is_someone_home()

        for room in self.rooms:
            for mode in CLEAN_MODES:
                task_key = _task_key(room, mode)
                if task_key in self._scheduled:
                    continue

                overdue_days = self._days_since_clean(room, mode, now)
                if overdue_days < self.max_days:
                    continue

                if someone_home:
                    await self._async_prompt_room(room, mode, overdue_days)
                    continue

                if not self._is_in_time_window(now):
                    _LOGGER.debug(
                        "Room %s (%s) is overdue but outside preferred time window",
                        room,
                        mode,
                    )
                    continue

                started = await self._async_start_room_clean(
                    room=room,
                    mode=mode,
                    reason="overdue_auto",
                )
                if started:
                    # Clean one room/mode task per cycle to avoid long chained runs.
                    return

    async def _async_prompt_room(self, room: str, mode: str, overdue_days: int) -> None:
        """Ask the user what to do with an overdue room/mode task."""
        now = dt_util.now()
        task_key = _task_key(room, mode)
        last_prompt = _parse_datetime(self._last_prompted.get(task_key))

        if (
            last_prompt is not None
            and now - last_prompt < timedelta(hours=PROMPT_COOLDOWN_HOURS)
        ):
            return

        self._last_prompted[task_key] = now.isoformat()
        await self._async_save_state()

        await self._async_speak(
            f"Room {room} has not been {_mode_past_participle(mode)} for "
            f"{overdue_days} days. Say 'jetzt' for now, 'in 2 Stunden', or "
            "'in 3 Tagen'."
        )

    async def _async_schedule_room(self, room: str, mode: str, when: datetime) -> None:
        """Schedule a room/mode clean with reminder."""
        when_utc = dt_util.as_utc(when)
        task_key = _task_key(room, mode)

        self._scheduled[task_key] = when_utc.isoformat()
        self._last_prompted.pop(task_key, None)

        self._set_scheduled_callbacks(room, mode, when_utc)
        await self._async_save_state()

        _LOGGER.info(
            "Scheduled room %s (%s) at %s for entry %s",
            room,
            mode,
            when_utc.isoformat(),
            self.entry.entry_id,
        )

    async def _async_handle_scheduled_start(self, room: str, mode: str) -> None:
        """Execute scheduled room/mode clean."""
        task_key = _task_key(room, mode)
        self._scheduled.pop(task_key, None)
        self._cancel_scheduled_callbacks(task_key)
        await self._async_save_state()

        now = dt_util.now()

        if not self._is_in_time_window(now):
            next_window = _next_window_start(now, self.window_start)
            await self._async_schedule_room(room, mode, next_window)
            await self._async_speak(
                f"{room} {_mode_speech(mode)} was outside the preferred cleaning "
                f"window. I moved it to "
                f"{dt_util.as_local(next_window).strftime('%H:%M')}."
            )
            return

        started = await self._async_start_room_clean(room, mode, reason="scheduled")
        if started:
            return

        # If presence blocked execution, retry later.
        retry_at = dt_util.now() + timedelta(minutes=30)
        await self._async_schedule_room(room, mode, retry_at)
        await self._async_speak(
            f"I could not start {room} {_mode_speech(mode)} because someone is home. "
            f"I will retry at {dt_util.as_local(retry_at).strftime('%H:%M')}."
        )

    async def _async_send_reminder(
        self, room: str, mode: str, scheduled_at: datetime
    ) -> None:
        """Send TTS reminder 10 minutes before scheduled clean."""
        await self._async_speak(
            f"Reminder: {room} is scheduled for {_mode_speech(mode)} at "
            f"{dt_util.as_local(scheduled_at).strftime('%H:%M')}. "
            "Please clear the path."
        )

    async def _async_start_room_clean(self, room: str, mode: str, reason: str) -> bool:
        """Start cleaning one room segment if conditions are met."""
        if self._is_someone_home():
            _LOGGER.info(
                "Skipping room %s (%s/%s): presence sensor indicates someone is home",
                room,
                mode,
                reason,
            )
            return False

        now = dt_util.now()
        if not self._is_in_time_window(now):
            _LOGGER.info(
                "Skipping room %s (%s/%s): outside configured time window",
                room,
                mode,
                reason,
            )
            return False

        segment_id = self.rooms[room]

        command_data = {
            "entity_id": self.vacuum_entity_id,
            "command": "clean_segment",
            "params": {"segments": [segment_id]},
        }

        try:
            await self.hass.services.async_call(
                vacuum.DOMAIN,
                vacuum.SERVICE_SEND_COMMAND,
                command_data,
                blocking=True,
            )
        except Exception as err:  # broad catch for integration-specific command variants
            _LOGGER.debug(
                "clean_segment with dict params failed for room %s (%s): %s",
                room,
                mode,
                err,
            )
            try:
                await self.hass.services.async_call(
                    vacuum.DOMAIN,
                    vacuum.SERVICE_SEND_COMMAND,
                    {
                        "entity_id": self.vacuum_entity_id,
                        "command": "clean_segment",
                        "params": [segment_id],
                    },
                    blocking=True,
                )
            except Exception as second_err:
                _LOGGER.warning(
                    "Segment cleaning failed for room %s (%s), "
                    "falling back to full start: %s",
                    room,
                    mode,
                    second_err,
                )
                try:
                    await self.hass.services.async_call(
                        vacuum.DOMAIN,
                        vacuum.SERVICE_START,
                        {"entity_id": self.vacuum_entity_id},
                        blocking=True,
                    )
                except HomeAssistantError as start_err:
                    _LOGGER.error(
                        "Failed to start vacuum for room %s (%s): %s",
                        room,
                        mode,
                        start_err,
                    )
                    return False

        task_key = _task_key(room, mode)
        self._last_cleaned[task_key] = dt_util.now().isoformat()
        self._last_prompted.pop(task_key, None)
        self._scheduled.pop(task_key, None)
        self._cancel_scheduled_callbacks(task_key)
        await self._async_save_state()

        _LOGGER.info("Started %s for room %s (%s)", _mode_speech(mode), room, reason)
        return True

    async def _async_speak(self, message: str) -> None:
        """Send TTS via configured service and media player."""
        if "." not in self.tts_service:
            _LOGGER.error("Invalid TTS service format: %s", self.tts_service)
            return

        domain, service = self.tts_service.split(".", 1)

        service_data = {
            "entity_id": self.media_player_entity_id,
            "media_player_entity_id": self.media_player_entity_id,
            "message": message,
        }

        try:
            await self.hass.services.async_call(
                domain,
                service,
                service_data,
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.error("Failed to send TTS message: %s", err)

    @callback
    def _async_state_changed_listener(self, event: Event) -> None:
        """Handle input_select changes used as quick response channel."""
        entity_id = event.data.get("entity_id", "")
        if not entity_id.startswith("input_select."):
            return

        new_state = event.data.get("new_state")
        if new_state is None:
            return

        response_text = str(new_state.state)
        if _parse_response_text(response_text) is None:
            return

        self.hass.async_create_task(self.async_handle_response(response=response_text))

    @callback
    def _async_custom_response_listener(self, event: Event) -> None:
        """Handle custom event payload from intent automations."""
        entry_id = event.data.get(ATTR_ENTRY_ID)
        if entry_id and entry_id != self.entry.entry_id:
            return

        response = event.data.get(ATTR_RESPONSE)
        if not response:
            return

        room = event.data.get(ATTR_ROOM)
        mode = event.data.get(ATTR_MODE)
        self.hass.async_create_task(
            self.async_handle_response(response=str(response), room=room, mode=mode)
        )

    def _set_scheduled_callbacks(self, room: str, mode: str, when: datetime) -> None:
        """Create reminder + start callbacks for one room/mode schedule."""
        task_key = _task_key(room, mode)
        self._cancel_scheduled_callbacks(task_key)

        unsubs: list[Callable[[], None]] = []

        reminder_time = when - timedelta(minutes=REMINDER_MINUTES)
        if reminder_time > dt_util.now():

            @callback
            def _async_reminder(_point_in_time: datetime) -> None:
                self.hass.async_create_task(self._async_send_reminder(room, mode, when))

            unsubs.append(
                async_track_point_in_time(self.hass, _async_reminder, reminder_time)
            )

        @callback
        def _async_start(_point_in_time: datetime) -> None:
            self.hass.async_create_task(self._async_handle_scheduled_start(room, mode))

        unsubs.append(async_track_point_in_time(self.hass, _async_start, when))

        self._scheduled_unsubs[task_key] = unsubs

    def _cancel_scheduled_callbacks(self, task_key: str) -> None:
        """Cancel callbacks for a room/mode task if present."""
        unsubs = self._scheduled_unsubs.pop(task_key, None)
        if not unsubs:
            return
        for unsub in unsubs:
            unsub()

    def _resolve_target(
        self, room: str | None, mode: str | None
    ) -> tuple[str, str] | None:
        """Resolve room/mode from direct input or latest prompt context."""
        target_room = self._resolve_room_name(room)
        if room is not None and target_room is None:
            return None

        if target_room is not None and mode is not None:
            return target_room, mode

        if target_room is not None and mode is None:
            prompted_for_room = self._prompted_targets(room_name=target_room, mode=None)
            if prompted_for_room:
                return prompted_for_room[0][0]
            return target_room, CLEAN_MODE_VACUUM

        if mode is not None and len(self.rooms) == 1:
            return next(iter(self.rooms)), mode

        prompted = self._prompted_targets(room_name=None, mode=mode)
        if prompted:
            return prompted[0][0]

        if len(self.rooms) == 1:
            return next(iter(self.rooms)), mode or CLEAN_MODE_VACUUM

        return None

    def _resolve_room_name(self, room: str | None) -> str | None:
        """Resolve canonical room name from direct input."""
        if room is None:
            return None
        for candidate in self.rooms:
            if candidate.casefold() == room.casefold():
                return candidate
        return None

    def _prompted_targets(
        self, room_name: str | None, mode: str | None
    ) -> list[tuple[tuple[str, str], datetime]]:
        """Return prompted tasks sorted by oldest first."""
        prompted: list[tuple[tuple[str, str], datetime]] = []

        for task_key, timestamp in self._last_prompted.items():
            target = _target_from_task_key(task_key, self.rooms)
            if target is None:
                continue

            target_room, target_mode = target
            if room_name is not None and target_room != room_name:
                continue
            if mode is not None and target_mode != mode:
                continue

            parsed = _parse_datetime(timestamp)
            if parsed is None:
                continue

            prompted.append((target, parsed))

        prompted.sort(key=lambda item: item[1])
        return prompted

    def _is_someone_home(self) -> bool:
        """Return True if the configured presence sensor says someone is home."""
        state = self.hass.states.get(self.presence_entity_id)
        if state is None:
            return True

        if state.state in {STATE_UNKNOWN, STATE_UNAVAILABLE}:
            return True

        return state.state in {STATE_ON, STATE_HOME}

    def _is_in_time_window(self, now: datetime) -> bool:
        """Check if a datetime is within configured preferred cleaning window."""
        now_time = dt_util.as_local(now).time().replace(second=0, microsecond=0)

        if self.window_start <= self.window_end:
            return self.window_start <= now_time <= self.window_end

        # Overnight window, e.g. 22:00-06:00
        return now_time >= self.window_start or now_time <= self.window_end

    def _days_since_clean(self, room: str, mode: str, now: datetime) -> int:
        """Return whole days since a room/mode task was last cleaned."""
        task_key = _task_key(room, mode)
        last_cleaned = _parse_datetime(self._last_cleaned.get(task_key))
        if last_cleaned is None:
            return self.max_days

        return max(0, (now - last_cleaned).days)


def _normalize_rooms(raw_rooms: Any) -> dict[str, int]:
    """Normalize room definitions from config data."""
    rooms: dict[str, int] = {}

    if not isinstance(raw_rooms, list):
        return rooms

    for entry in raw_rooms:
        if not isinstance(entry, dict):
            continue

        name = str(entry.get(CONF_ROOM_NAME, "")).strip()
        segment = entry.get(CONF_SEGMENT_ID)

        if not name:
            continue

        try:
            segment_id = int(segment)
        except (TypeError, ValueError):
            continue

        rooms[name] = segment_id

    return rooms


def _task_key(room: str, mode: str) -> str:
    """Build stable storage key for one room/mode task."""
    return f"{room}::{mode}"


def _target_from_task_key(
    task_key: str, rooms: dict[str, int]
) -> tuple[str, str] | None:
    """Extract room + mode from a persisted task key."""
    if not isinstance(task_key, str):
        return None
    parts = task_key.rsplit("::", 1)
    if len(parts) != 2:
        return None

    room, raw_mode = parts
    mode = _normalize_clean_mode(raw_mode)
    if room not in rooms or mode is None:
        return None
    return room, mode


def _normalize_task_state(raw_state: Any, rooms: dict[str, int]) -> dict[str, str]:
    """Normalize persisted task-keyed datetime dictionaries."""
    normalized: dict[str, str] = {}
    if not isinstance(raw_state, dict):
        return normalized

    for task, value in raw_state.items():
        if not isinstance(value, str):
            continue
        target = _target_from_task_key(task, rooms)
        if target is None:
            continue
        room, mode = target
        normalized[_task_key(room, mode)] = value

    return normalized


def _normalize_clean_mode(value: Any) -> str | None:
    """Normalize mode aliases to vacuum or mop."""
    if not isinstance(value, str):
        return None
    return MODE_ALIASES.get(value.strip().casefold())


def _parse_mode_from_text(response: str) -> str | None:
    """Parse cleaning mode from free-form response text."""
    tokens = TOKEN_RE.findall(response.casefold())
    for token in tokens:
        mode = _normalize_clean_mode(token)
        if mode is not None:
            return mode
    return None


def _mode_speech(mode: str) -> str:
    """Return human-friendly continuous label for mode."""
    return MODE_SPEECH_LABELS.get(mode, mode)


def _mode_past_participle(mode: str) -> str:
    """Return human-friendly past participle label for mode."""
    return MODE_PAST_PARTICIPLES.get(mode, mode)


def _parse_response_text(response: str) -> str | timedelta | None:
    """Parse voice response text."""
    normalized = response.strip().casefold()
    tokens = set(TOKEN_RE.findall(normalized))

    if NOW_WORDS.intersection(tokens):
        return "now"

    hour_match = HOURS_RE.search(normalized)
    if hour_match:
        return timedelta(hours=max(1, int(hour_match.group(1))))

    day_match = DAYS_RE.search(normalized)
    if day_match:
        return timedelta(days=max(1, int(day_match.group(1))))

    return None


def _parse_time(value: Any, default_value: str) -> time:
    """Parse HH:MM(:SS) time values from config entry."""
    parsed = dt_util.parse_time(str(value)) if value is not None else None
    if parsed is None:
        parsed = dt_util.parse_time(default_value)
    assert parsed is not None
    return parsed.replace(second=0, microsecond=0)


def _parse_datetime(value: Any) -> datetime | None:
    """Parse datetime values from storage."""
    if not isinstance(value, str):
        return None
    parsed = dt_util.parse_datetime(value)
    if parsed is None:
        return None
    return dt_util.as_utc(parsed)


def _next_window_start(now: datetime, window_start: time) -> datetime:
    """Calculate next datetime at configured window start."""
    local_now = dt_util.as_local(now)
    candidate = local_now.replace(
        hour=window_start.hour,
        minute=window_start.minute,
        second=0,
        microsecond=0,
    )

    if candidate <= local_now:
        candidate = candidate + timedelta(days=1)

    return dt_util.as_utc(candidate)
