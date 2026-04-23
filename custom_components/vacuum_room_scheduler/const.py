"""Constants for Vacuum Room Scheduler."""

from __future__ import annotations

DOMAIN = "vacuum_room_scheduler"

CONF_VACUUM_ENTITY_ID = "vacuum_entity_id"
CONF_PRESENCE_ENTITY_ID = "presence_entity_id"
CONF_TTS_SERVICE = "tts_service"
CONF_MEDIA_PLAYER_ENTITY_ID = "media_player_entity_id"
CONF_ROOMS = "rooms"
CONF_ROOM_NAME = "name"
CONF_SEGMENT_ID = "segment_id"
CONF_MAX_DAYS = "max_days"
CONF_WINDOW_START = "window_start"
CONF_WINDOW_END = "window_end"

DEFAULT_MAX_DAYS = 7
DEFAULT_WINDOW_START = "09:00:00"
DEFAULT_WINDOW_END = "17:00:00"

CHECK_INTERVAL_MINUTES = 30
PROMPT_COOLDOWN_HOURS = 12
REMINDER_MINUTES = 10

STORAGE_VERSION = 1

DATA_MANAGERS = "managers"
DATA_SERVICES_REGISTERED = "services_registered"

EVENT_RESPONSE = "vacuum_room_scheduler_response"

SERVICE_HANDLE_RESPONSE = "handle_response"

ATTR_ENTRY_ID = "entry_id"
ATTR_RESPONSE = "response"
ATTR_ROOM = "room"
ATTR_MODE = "mode"
