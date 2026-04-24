"""Config flow for Vacuum Room Scheduler."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
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
    DEFAULT_MAX_DAYS,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DOMAIN,
)
from .room_discovery import discover_rooms_on_same_floor

ACTION_ADD_ROOM = "add_room"
ACTION_REMOVE_ROOM = "remove_room"
ACTION_DISCOVER_ROOMS = "discover_rooms"
ACTION_DONE = "done"
ACTION_FIELD = "action"
ACTION_REMOVE_FIELD = "room_to_remove"


class VacuumRoomSchedulerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Vacuum Room Scheduler."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow."""
        self._base_data: dict[str, Any] = {}
        self._rooms: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return options flow handler."""
        return VacuumRoomSchedulerOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not _is_valid_tts_service(user_input[CONF_TTS_SERVICE]):
                errors["base"] = "invalid_tts_service"
            else:
                self._base_data = user_input
                self._rooms = _discover_rooms_for_vacuum(
                    self.hass, user_input[CONF_VACUUM_ENTITY_ID]
                )
                unique_id = (
                    f"{user_input[CONF_VACUUM_ENTITY_ID]}"
                    f"::{user_input[CONF_PRESENCE_ENTITY_ID]}"
                )
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return await self.async_step_rooms_menu()

        return self.async_show_form(
            step_id="user",
            data_schema=_build_base_schema(self._base_data),
            errors=errors,
        )

    async def async_step_rooms_menu(self, user_input: dict[str, Any] | None = None):
        """Edit room list in a dynamic add/remove loop."""
        errors: dict[str, str] = {}

        if user_input is not None:
            action = user_input[ACTION_FIELD]
            if action == ACTION_ADD_ROOM:
                return await self.async_step_room_add()
            if action == ACTION_REMOVE_ROOM:
                return await self.async_step_room_remove()
            if action == ACTION_DISCOVER_ROOMS:
                return await self.async_step_room_discover()
            if action == ACTION_DONE:
                if not self._rooms:
                    discovered = _discover_rooms_for_vacuum(
                        self.hass, self._base_data[CONF_VACUUM_ENTITY_ID]
                    )
                    if discovered:
                        self._rooms = discovered
                    else:
                        errors["base"] = "no_rooms"
                if self._rooms:
                    return self.async_create_entry(
                        title="Vacuum Room Scheduler",
                        data={**self._base_data, CONF_ROOMS: self._rooms},
                    )

        options = [
            selector.SelectOptionDict(value=ACTION_ADD_ROOM, label="Add room"),
            selector.SelectOptionDict(
                value=ACTION_DISCOVER_ROOMS,
                label="Discover rooms from Home Assistant",
            ),
            selector.SelectOptionDict(value=ACTION_DONE, label="Finish setup"),
        ]

        if self._rooms:
            options.insert(
                1,
                selector.SelectOptionDict(
                    value=ACTION_REMOVE_ROOM,
                    label="Remove room",
                ),
            )

        existing_rooms = ", ".join(
            f"{room[CONF_ROOM_NAME]} (segment {room[CONF_SEGMENT_ID]})"
            for room in self._rooms
        )

        return self.async_show_form(
            step_id="rooms_menu",
            data_schema=vol.Schema(
                {
                    vol.Required(ACTION_FIELD): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "rooms": existing_rooms or "No rooms configured yet.",
            },
        )

    async def async_step_room_add(self, user_input: dict[str, Any] | None = None):
        """Add one room and return to room menu."""
        errors: dict[str, str] = {}

        if user_input is not None:
            room_name = user_input[CONF_ROOM_NAME].strip()
            segment_id = int(user_input[CONF_SEGMENT_ID])

            duplicate = any(
                room[CONF_ROOM_NAME].casefold() == room_name.casefold()
                for room in self._rooms
            )
            if duplicate:
                errors["base"] = "duplicate_room"
            else:
                self._rooms.append(
                    {
                        CONF_ROOM_NAME: room_name,
                        CONF_SEGMENT_ID: segment_id,
                    }
                )
                return await self.async_step_rooms_menu()

        return self.async_show_form(
            step_id="room_add",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROOM_NAME): selector.TextSelector(
                        selector.TextSelectorConfig()
                    ),
                    vol.Required(CONF_SEGMENT_ID): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=9999,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_room_remove(self, user_input: dict[str, Any] | None = None):
        """Remove one room and return to room menu."""
        if not self._rooms:
            return await self.async_step_rooms_menu()

        if user_input is not None:
            room_name = user_input[ACTION_REMOVE_FIELD]
            self._rooms = [
                room for room in self._rooms if room[CONF_ROOM_NAME] != room_name
            ]
            return await self.async_step_rooms_menu()

        return self.async_show_form(
            step_id="room_remove",
            data_schema=vol.Schema(
                {
                    vol.Required(ACTION_REMOVE_FIELD): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=room[CONF_ROOM_NAME],
                                    label=(
                                        f"{room[CONF_ROOM_NAME]} "
                                        f"(segment {room[CONF_SEGMENT_ID]})"
                                    ),
                                )
                                for room in self._rooms
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_room_discover(self, user_input: dict[str, Any] | None = None):
        """Discover rooms from Home Assistant and return to room menu."""
        del user_input
        self._rooms = _discover_rooms_for_vacuum(
            self.hass, self._base_data[CONF_VACUUM_ENTITY_ID]
        )
        return await self.async_step_rooms_menu()


class VacuumRoomSchedulerOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Vacuum Room Scheduler."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry
        merged_data = {**config_entry.data, **config_entry.options}
        self._base_data = {
            CONF_VACUUM_ENTITY_ID: merged_data.get(CONF_VACUUM_ENTITY_ID, ""),
            CONF_PRESENCE_ENTITY_ID: merged_data.get(CONF_PRESENCE_ENTITY_ID, ""),
            CONF_TTS_SERVICE: merged_data.get(CONF_TTS_SERVICE, ""),
            CONF_MEDIA_PLAYER_ENTITY_ID: merged_data.get(CONF_MEDIA_PLAYER_ENTITY_ID, ""),
            CONF_MAX_DAYS: int(merged_data.get(CONF_MAX_DAYS, DEFAULT_MAX_DAYS)),
            CONF_WINDOW_START: merged_data.get(CONF_WINDOW_START, DEFAULT_WINDOW_START),
            CONF_WINDOW_END: merged_data.get(CONF_WINDOW_END, DEFAULT_WINDOW_END),
        }
        self._rooms: list[dict[str, Any]] = _normalize_rooms(merged_data.get(CONF_ROOMS, []))

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage the options."""
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Edit base integration settings."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if not _is_valid_tts_service(user_input[CONF_TTS_SERVICE]):
                errors["base"] = "invalid_tts_service"
            else:
                self._base_data = user_input
                if not self._rooms:
                    self._rooms = _discover_rooms_for_vacuum(
                        self.hass, self._base_data[CONF_VACUUM_ENTITY_ID]
                    )
                return await self.async_step_rooms_menu()

        return self.async_show_form(
            step_id="user",
            data_schema=_build_base_schema(self._base_data),
            errors=errors,
        )

    async def async_step_rooms_menu(self, user_input: dict[str, Any] | None = None):
        """Edit room list in options flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            action = user_input[ACTION_FIELD]
            if action == ACTION_ADD_ROOM:
                return await self.async_step_room_add()
            if action == ACTION_REMOVE_ROOM:
                return await self.async_step_room_remove()
            if action == ACTION_DISCOVER_ROOMS:
                return await self.async_step_room_discover()
            if action == ACTION_DONE:
                if not self._rooms:
                    discovered = _discover_rooms_for_vacuum(
                        self.hass, self._base_data[CONF_VACUUM_ENTITY_ID]
                    )
                    if discovered:
                        self._rooms = discovered
                    else:
                        errors["base"] = "no_rooms"
                if self._rooms:
                    return self.async_create_entry(
                        title="",
                        data={**self._base_data, CONF_ROOMS: self._rooms},
                    )

        options = [
            selector.SelectOptionDict(value=ACTION_ADD_ROOM, label="Add room"),
            selector.SelectOptionDict(
                value=ACTION_DISCOVER_ROOMS,
                label="Discover rooms from Home Assistant",
            ),
            selector.SelectOptionDict(value=ACTION_DONE, label="Save options"),
        ]
        if self._rooms:
            options.insert(
                1,
                selector.SelectOptionDict(
                    value=ACTION_REMOVE_ROOM,
                    label="Remove room",
                ),
            )

        existing_rooms = ", ".join(
            f"{room[CONF_ROOM_NAME]} (segment {room[CONF_SEGMENT_ID]})"
            for room in self._rooms
        )

        return self.async_show_form(
            step_id="rooms_menu",
            data_schema=vol.Schema(
                {
                    vol.Required(ACTION_FIELD): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=options,
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={
                "rooms": existing_rooms or "No rooms configured yet.",
            },
        )

    async def async_step_room_add(self, user_input: dict[str, Any] | None = None):
        """Add a room in options flow."""
        errors: dict[str, str] = {}

        if user_input is not None:
            room_name = user_input[CONF_ROOM_NAME].strip()
            segment_id = int(user_input[CONF_SEGMENT_ID])

            duplicate = any(
                room[CONF_ROOM_NAME].casefold() == room_name.casefold()
                for room in self._rooms
            )
            if duplicate:
                errors["base"] = "duplicate_room"
            else:
                self._rooms.append(
                    {
                        CONF_ROOM_NAME: room_name,
                        CONF_SEGMENT_ID: segment_id,
                    }
                )
                return await self.async_step_rooms_menu()

        return self.async_show_form(
            step_id="room_add",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ROOM_NAME): selector.TextSelector(
                        selector.TextSelectorConfig()
                    ),
                    vol.Required(CONF_SEGMENT_ID): selector.NumberSelector(
                        selector.NumberSelectorConfig(
                            min=1,
                            max=9999,
                            step=1,
                            mode=selector.NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_room_remove(self, user_input: dict[str, Any] | None = None):
        """Remove a room in options flow."""
        if not self._rooms:
            return await self.async_step_rooms_menu()

        if user_input is not None:
            room_name = user_input[ACTION_REMOVE_FIELD]
            self._rooms = [
                room for room in self._rooms if room[CONF_ROOM_NAME] != room_name
            ]
            return await self.async_step_rooms_menu()

        return self.async_show_form(
            step_id="room_remove",
            data_schema=vol.Schema(
                {
                    vol.Required(ACTION_REMOVE_FIELD): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                selector.SelectOptionDict(
                                    value=room[CONF_ROOM_NAME],
                                    label=(
                                        f"{room[CONF_ROOM_NAME]} "
                                        f"(segment {room[CONF_SEGMENT_ID]})"
                                    ),
                                )
                                for room in self._rooms
                            ],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_room_discover(self, user_input: dict[str, Any] | None = None):
        """Discover rooms from Home Assistant and return to room menu."""
        del user_input
        self._rooms = _discover_rooms_for_vacuum(
            self.hass, self._base_data[CONF_VACUUM_ENTITY_ID]
        )
        return await self.async_step_rooms_menu()


def _build_base_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Build schema for shared base settings."""
    return vol.Schema(
        {
            vol.Required(
                CONF_VACUUM_ENTITY_ID,
                default=defaults.get(CONF_VACUUM_ENTITY_ID, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="vacuum")
            ),
            vol.Required(
                CONF_PRESENCE_ENTITY_ID,
                default=defaults.get(CONF_PRESENCE_ENTITY_ID, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor")
            ),
            vol.Required(
                CONF_TTS_SERVICE,
                default=defaults.get(CONF_TTS_SERVICE, "tts.google_translate_say"),
            ): selector.TextSelector(selector.TextSelectorConfig()),
            vol.Required(
                CONF_MEDIA_PLAYER_ENTITY_ID,
                default=defaults.get(CONF_MEDIA_PLAYER_ENTITY_ID, ""),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player")
            ),
            vol.Required(
                CONF_MAX_DAYS,
                default=int(defaults.get(CONF_MAX_DAYS, DEFAULT_MAX_DAYS)),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1,
                    max=365,
                    step=1,
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_WINDOW_START,
                default=defaults.get(CONF_WINDOW_START, DEFAULT_WINDOW_START),
            ): selector.TimeSelector(selector.TimeSelectorConfig()),
            vol.Required(
                CONF_WINDOW_END,
                default=defaults.get(CONF_WINDOW_END, DEFAULT_WINDOW_END),
            ): selector.TimeSelector(selector.TimeSelectorConfig()),
        }
    )


def _normalize_rooms(raw_rooms: Any) -> list[dict[str, Any]]:
    """Normalize room config from config/option data."""
    rooms: list[dict[str, Any]] = []
    if not isinstance(raw_rooms, list):
        return rooms

    for room in raw_rooms:
        if not isinstance(room, dict):
            continue
        name = str(room.get(CONF_ROOM_NAME, "")).strip()
        segment = room.get(CONF_SEGMENT_ID)
        if not name:
            continue
        try:
            segment_id = int(segment)
        except (TypeError, ValueError):
            continue
        rooms.append({CONF_ROOM_NAME: name, CONF_SEGMENT_ID: segment_id})

    return rooms


def _is_valid_tts_service(value: str) -> bool:
    """Validate a service string in the form domain.service."""
    if not value or "." not in value:
        return False
    domain, service = value.split(".", 1)
    return bool(domain.strip() and service.strip())


def _discover_rooms_for_vacuum(
    hass: HomeAssistant, vacuum_entity_id: str
) -> list[dict[str, Any]]:
    """Discover room mapping from HA and filter to same floor as the vacuum."""
    discovered_rooms, _ = discover_rooms_on_same_floor(hass, vacuum_entity_id)
    return [
        {CONF_ROOM_NAME: room_name, CONF_SEGMENT_ID: segment_id}
        for room_name, segment_id in sorted(discovered_rooms.items())
    ]
