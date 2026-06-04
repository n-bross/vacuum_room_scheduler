#!/usr/bin/env python3
"""Debug room discovery against Home Assistant storage files.

Usage:
  python3 scripts/discover_rooms_debug.py \
    --ha-config /path/to/homeassistant/config \
    --vacuum vacuum.my_robot

Optional:
  --state-file /path/to/exported_state.json

The script reads:
  - .storage/core.area_registry
  - .storage/core.floor_registry
  - .storage/core.entity_registry
  - .storage/core.device_registry
  - .storage/core.restore_state or an explicit --state-file

It prints:
  - the vacuum area/floor
  - all areas on the same floor
  - the room->segment map found on the vacuum
  - the final filtered room list
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class AreaEntry:
    area_id: str
    name: str
    floor_id: str | None


def main() -> int:
    args = parse_args()
    storage = Path(args.ha_config) / ".storage"

    area_registry = load_storage_json(storage / "core.area_registry")
    floor_registry = load_storage_json(storage / "core.floor_registry")
    entity_registry = load_storage_json(storage / "core.entity_registry")
    device_registry = load_storage_json(storage / "core.device_registry")
    state_source = load_state_source(args.state_file, storage / "core.restore_state")

    area_entries = parse_area_registry(area_registry)
    floor_names = parse_floor_registry(floor_registry)
    vacuum_entity = args.vacuum

    vacuum_state = find_state(state_source, vacuum_entity)
    if vacuum_state is None:
        print(f"ERROR: no state found for {vacuum_entity}")
        return 2

    vacuum_area_id = resolve_entity_area_id(
        entity_registry, device_registry, vacuum_entity
    )
    vacuum_area = area_entries.get(vacuum_area_id or "")
    if vacuum_area is None:
        print(f"Vacuum entity: {vacuum_entity}")
        print("Vacuum area: <unknown>")
    else:
        floor_name = floor_names.get(vacuum_area.floor_id or "", "<none>")
        print(f"Vacuum entity: {vacuum_entity}")
        print(f"Vacuum area: {vacuum_area.name} (floor: {floor_name})")

    segments = discover_room_segments(vacuum_state)
    print()
    print("Room mapping found on vacuum:")
    if segments:
        for room_name, segment_id in sorted(segments.items()):
            print(f"  - {room_name}: segment {segment_id}")
    else:
        print("  <none>")

    same_floor_areas = []
    if vacuum_area and vacuum_area.floor_id:
        same_floor_areas = [
            area
            for area in area_entries.values()
            if area.floor_id == vacuum_area.floor_id
        ]

    print()
    print("Areas on the same floor:")
    if same_floor_areas:
        for area in sorted(same_floor_areas, key=lambda item: item.name.casefold()):
            print(f"  - {area.name} (area_id: {area.area_id}, floor_id: {area.floor_id})")
    else:
        print("  <none>")

    print()
    print("Filtered rooms:")
    if same_floor_areas:
        allowed = {
            normalize_name(area.name)
            for area in same_floor_areas
            if area.name
        }
        filtered = {
            room_name: segment_id
            for room_name, segment_id in segments.items()
            if normalize_name(room_name) in allowed
        }
    else:
        filtered = dict(segments)

    if filtered:
        for room_name, segment_id in sorted(filtered.items()):
            print(f"  - {room_name}: segment {segment_id}")
    else:
        print("  <none>")

    unmatched = sorted(set(segments) - set(filtered))
    if unmatched:
        print()
        print("Unmatched discovered room names:")
        for name in unmatched:
            print(f"  - {name}")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ha-config", required=True, help="Home Assistant config dir")
    parser.add_argument("--vacuum", required=True, help="Vacuum entity_id")
    parser.add_argument(
        "--state-file",
        help="Optional JSON file with state export for testing",
    )
    return parser.parse_args()


def load_storage_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_state_source(explicit_state_file: str | None, fallback_path: Path) -> dict[str, Any]:
    if explicit_state_file:
        path = Path(explicit_state_file)
        if path.exists():
            return load_storage_json(path)

    fallback = load_storage_json(fallback_path)
    return fallback


def parse_area_registry(data: dict[str, Any]) -> dict[str, AreaEntry]:
    areas: dict[str, AreaEntry] = {}
    raw_areas = data.get("data", {}).get("areas", [])
    for area in raw_areas:
        if not isinstance(area, dict):
            continue
        area_id = str(area.get("area_id", "")).strip()
        name = str(area.get("name", "")).strip()
        floor_id = area.get("floor_id")
        floor_id = str(floor_id).strip() if floor_id not in (None, "") else None
        if area_id and name:
            areas[area_id] = AreaEntry(area_id=area_id, name=name, floor_id=floor_id)
    return areas


def parse_floor_registry(data: dict[str, Any]) -> dict[str, str]:
    floors: dict[str, str] = {}
    raw_floors = data.get("data", {}).get("floors", [])
    for floor in raw_floors:
        if not isinstance(floor, dict):
            continue
        floor_id = str(floor.get("floor_id", "")).strip()
        name = str(floor.get("name", "")).strip()
        if floor_id and name:
            floors[floor_id] = name
    return floors


def resolve_entity_area_id(
    entity_registry: dict[str, Any],
    device_registry: dict[str, Any],
    entity_id: str,
) -> str | None:
    entities = entity_registry.get("data", {}).get("entities", [])
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if entity.get("entity_id") != entity_id:
            continue
        area_id = entity.get("area_id")
        if area_id:
            return str(area_id)
        device_id = entity.get("device_id")
        if not device_id:
            return None
        return resolve_device_area_id(device_registry, str(device_id))
    return None


def resolve_device_area_id(device_registry: dict[str, Any], device_id: str) -> str | None:
    devices = device_registry.get("data", {}).get("devices", [])
    for device in devices:
        if not isinstance(device, dict):
            continue
        if device.get("id") != device_id:
            continue
        area_id = device.get("area_id")
        if area_id:
            return str(area_id)
    return None


def find_state(state_source: dict[str, Any], entity_id: str) -> dict[str, Any] | None:
    data = state_source.get("data", {})

    states = data.get("states")
    if isinstance(states, dict):
        state = states.get(entity_id)
        if isinstance(state, dict):
            return state

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("entity_id") == entity_id:
                return item

    for key in ("states", "state"):
        candidate = data.get(key)
        if isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, dict) and item.get("entity_id") == entity_id:
                    return item

    return None


def discover_room_segments(vacuum_state: dict[str, Any]) -> dict[str, int]:
    attrs = vacuum_state.get("attributes", {})
    if not isinstance(attrs, dict):
        return {}

    for key in ("room_mapping", "rooms", "segments", "segment_map", "room_map"):
        value = attrs.get(key)
        mapping = coerce_room_mapping(value)
        if mapping:
            return mapping

    for value in attrs.values():
        mapping = coerce_room_mapping(value)
        if mapping:
            return mapping

    return {}


def coerce_room_mapping(raw_value: Any) -> dict[str, int]:
    if isinstance(raw_value, dict):
        mapping = mapping_from_dict(raw_value)
        if mapping:
            return mapping
    if isinstance(raw_value, (list, tuple, set)):
        mapping = mapping_from_iterable(raw_value)
        if mapping:
            return mapping
    return {}


def mapping_from_dict(raw_mapping: dict[Any, Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for raw_key, raw_value in raw_mapping.items():
        key_name = as_room_name(raw_key)
        key_id = as_segment_id(raw_key)
        value_name = as_room_name(raw_value)
        value_id = as_segment_id(raw_value)
        if key_name is not None and value_id is not None:
            mapping[key_name] = value_id
            continue
        if value_name is not None and key_id is not None:
            mapping[value_name] = key_id
    return mapping


def mapping_from_iterable(raw_items: list[Any] | tuple[Any, ...] | set[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for item in raw_items:
        pair = pair_from_item(item)
        if pair is not None:
            room_name, segment_id = pair
            mapping[room_name] = segment_id
    return mapping


def pair_from_item(item: Any) -> tuple[str, int] | None:
    if isinstance(item, dict):
        name = first_room_name(
            item.get("name"),
            item.get("room"),
            item.get("room_name"),
            item.get("segment_name"),
            item.get("label"),
        )
        segment_id = first_segment_id(
            item.get("segment_id"),
            item.get("segment"),
            item.get("id"),
            item.get("room_id"),
        )
        if name is not None and segment_id is not None:
            return name, segment_id
        return None

    if isinstance(item, (list, tuple)) and len(item) >= 2:
        first_name = as_room_name(item[0])
        first_id = as_segment_id(item[0])
        second_name = as_room_name(item[1])
        second_id = as_segment_id(item[1])
        if first_name is not None and second_id is not None:
            return first_name, second_id
        if second_name is not None and first_id is not None:
            return second_name, first_id

    return None


def first_room_name(*values: Any) -> str | None:
    for value in values:
        name = as_room_name(value)
        if name is not None:
            return name
    return None


def first_segment_id(*values: Any) -> int | None:
    for value in values:
        segment_id = as_segment_id(value)
        if segment_id is not None:
            return segment_id
    return None


def as_room_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def as_segment_id(value: Any) -> int | None:
    try:
        segment_id = int(value)
    except (TypeError, ValueError):
        return None
    if segment_id < 0:
        return None
    return segment_id


def normalize_name(value: str) -> str:
    return value.strip().casefold()


if __name__ == "__main__":
    raise SystemExit(main())
