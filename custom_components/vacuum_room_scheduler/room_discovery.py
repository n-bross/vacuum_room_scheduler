"""Room discovery helpers for Vacuum Room Scheduler."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


def discover_floor_area_names(
    hass: HomeAssistant, entity_id: str
) -> tuple[set[str], str | None]:
    """Return area names on the same floor as the given entity."""
    area_id = _resolve_entity_area_id(hass, entity_id)
    if area_id is None:
        return set(), None

    area_reg = ar.async_get(hass)
    area_entry = area_reg.async_get_area(area_id)
    if area_entry is None:
        return set(), None

    floor_id = getattr(area_entry, "floor_id", None)
    if not floor_id:
        return set(), None

    names: set[str] = set()
    for candidate in area_reg.areas.values():
        if getattr(candidate, "floor_id", None) != floor_id:
            continue
        name = (candidate.name or "").strip()
        if name:
            names.add(name)

    return names, area_entry.name


def discover_vacuum_segment_map(hass: HomeAssistant, vacuum_entity_id: str) -> dict[str, int]:
    """Try to discover room-name to segment-id mapping from vacuum state attributes."""
    state = hass.states.get(vacuum_entity_id)
    if state is None:
        return {}

    attrs = dict(state.attributes)
    preferred_keys = (
        "room_mapping",
        "rooms",
        "segments",
        "segment_map",
        "room_map",
    )

    for key in preferred_keys:
        if key not in attrs:
            continue
        mapping = _coerce_room_mapping(attrs[key])
        if mapping:
            return mapping

    for value in attrs.values():
        mapping = _coerce_room_mapping(value)
        if mapping:
            return mapping

    return {}


def filter_rooms_by_allowed_names(
    rooms: Mapping[str, int], allowed_names: set[str]
) -> dict[str, int]:
    """Filter room mapping by case-insensitive allowed room names."""
    if not allowed_names:
        return dict(rooms)

    allowed_lookup = {_normalize_name(name) for name in allowed_names if name}
    return {
        room_name: segment_id
        for room_name, segment_id in rooms.items()
        if _normalize_name(room_name) in allowed_lookup
    }


def discover_rooms_on_same_floor(
    hass: HomeAssistant, vacuum_entity_id: str
) -> tuple[dict[str, int], str | None]:
    """Discover vacuum rooms and keep only rooms on same floor as the vacuum."""
    discovered = discover_vacuum_segment_map(hass, vacuum_entity_id)
    if not discovered:
        return {}, None

    allowed_names, anchor_area_name = discover_floor_area_names(hass, vacuum_entity_id)
    if not allowed_names:
        return discovered, anchor_area_name

    return filter_rooms_by_allowed_names(discovered, allowed_names), anchor_area_name


def _resolve_entity_area_id(hass: HomeAssistant, entity_id: str) -> str | None:
    """Resolve area id from entity or its device."""
    entity_reg = er.async_get(hass)
    entry = entity_reg.async_get(entity_id)
    if entry is None:
        return None

    if entry.area_id:
        return entry.area_id

    if not entry.device_id:
        return None

    device_reg = dr.async_get(hass)
    device = device_reg.async_get(entry.device_id)
    if device is None:
        return None

    return device.area_id


def _coerce_room_mapping(raw_value: Any) -> dict[str, int]:
    """Convert different room-mapping shapes to room-name->segment-id."""
    if isinstance(raw_value, Mapping):
        mapping = _mapping_from_dict(raw_value)
        if mapping:
            return mapping

    if isinstance(raw_value, (list, tuple, set)):
        mapping = _mapping_from_iterable(raw_value)
        if mapping:
            return mapping

    return {}


def _mapping_from_dict(raw_mapping: Mapping[Any, Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}

    for raw_key, raw_value in raw_mapping.items():
        key_name = _as_room_name(raw_key)
        key_id = _as_segment_id(raw_key)
        value_name = _as_room_name(raw_value)
        value_id = _as_segment_id(raw_value)

        if key_name is not None and value_id is not None:
            mapping[key_name] = value_id
            continue

        if value_name is not None and key_id is not None:
            mapping[value_name] = key_id

    return mapping


def _mapping_from_iterable(raw_items: list[Any] | tuple[Any, ...] | set[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}

    for item in raw_items:
        pair = _pair_from_item(item)
        if pair is None:
            continue
        room_name, segment_id = pair
        mapping[room_name] = segment_id

    return mapping


def _pair_from_item(item: Any) -> tuple[str, int] | None:
    if isinstance(item, Mapping):
        name = _first_room_name(
            item.get("name"),
            item.get("room"),
            item.get("room_name"),
            item.get("segment_name"),
            item.get("label"),
        )
        segment_id = _first_segment_id(
            item.get("segment_id"),
            item.get("segment"),
            item.get("id"),
            item.get("room_id"),
        )
        if name is not None and segment_id is not None:
            return name, segment_id
        return None

    if isinstance(item, (list, tuple)) and len(item) >= 2:
        first_name = _as_room_name(item[0])
        first_id = _as_segment_id(item[0])
        second_name = _as_room_name(item[1])
        second_id = _as_segment_id(item[1])

        if first_name is not None and second_id is not None:
            return first_name, second_id
        if second_name is not None and first_id is not None:
            return second_name, first_id

    return None


def _first_room_name(*values: Any) -> str | None:
    for value in values:
        name = _as_room_name(value)
        if name is not None:
            return name
    return None


def _first_segment_id(*values: Any) -> int | None:
    for value in values:
        segment_id = _as_segment_id(value)
        if segment_id is not None:
            return segment_id
    return None


def _as_room_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned


def _as_segment_id(value: Any) -> int | None:
    try:
        segment_id = int(value)
    except (TypeError, ValueError):
        return None
    if segment_id < 0:
        return None
    return segment_id


def _normalize_name(value: str) -> str:
    return value.strip().casefold()
