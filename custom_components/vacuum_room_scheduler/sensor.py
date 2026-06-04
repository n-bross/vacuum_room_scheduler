"""Sensor platform for Vacuum Room Scheduler."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import dt as dt_util

from . import (
    CLEAN_MODE_MOP,
    CLEAN_MODE_VACUUM,
    VacuumRoomSchedulerManager,
)
from .const import DATA_MANAGERS, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up room status sensors from a config entry."""
    manager = hass.data[DOMAIN][DATA_MANAGERS].get(entry.entry_id)
    if manager is None:
        return

    entities: list[SensorEntity] = []
    for room_name in manager.rooms:
        entities.append(VacuumRoomLastCleanedSensor(manager, room_name, CLEAN_MODE_VACUUM))
        entities.append(VacuumRoomLastCleanedSensor(manager, room_name, CLEAN_MODE_MOP))
        entities.append(VacuumRoomNextPlannedSensor(manager, room_name, CLEAN_MODE_VACUUM))
        entities.append(VacuumRoomNextPlannedSensor(manager, room_name, CLEAN_MODE_MOP))

    async_add_entities(entities)


class VacuumRoomLastCleanedSensor(SensorEntity):
    """Timestamp sensor for a room/mode pair."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True

    def __init__(
        self,
        manager: VacuumRoomSchedulerManager,
        room_name: str,
        mode: str,
    ) -> None:
        """Initialize the sensor."""
        self._manager = manager
        self._room_name = room_name
        self._mode = mode
        self._unsub_manager: Callable[[], None] | None = None

        mode_label = "vacuumed" if mode == CLEAN_MODE_VACUUM else "mopped"
        self._attr_name = f"{room_name} last {mode_label}"
        self._attr_unique_id = (
            f"{manager.entry.entry_id}_{room_name}_{mode}_last_cleaned"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry.entry_id)},
            name="Vacuum Room Scheduler",
            manufacturer="nbross",
            model="room scheduler",
        )

    async def async_added_to_hass(self) -> None:
        """Register for manager updates."""
        self._unsub_manager = self._manager.register_state_listener(self._handle_update)
        self.async_on_remove(self._remove_manager_listener)
        self.async_write_ha_state()

    @callback
    def _handle_update(self) -> None:
        """Update the sensor when the manager changes."""
        self.async_write_ha_state()

    @callback
    def _remove_manager_listener(self) -> None:
        """Remove the manager listener if present."""
        if self._unsub_manager is not None:
            self._unsub_manager()
            self._unsub_manager = None

    @property
    def native_value(self) -> datetime | None:
        """Return the last cleaned timestamp."""
        return self._manager.get_last_cleaned(self._room_name, self._mode)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose supporting room metadata."""
        last_prompted = self._manager.get_last_prompted(self._room_name, self._mode)
        scheduled = self._manager.get_scheduled(self._room_name, self._mode)

        attrs: dict[str, Any] = {
            "room": self._room_name,
            "mode": self._mode,
            "segment_id": self._manager.get_room_segment_id(self._room_name),
            "area_id": self._manager.get_room_area_id(self._room_name),
            "last_prompted": last_prompted.isoformat() if last_prompted else None,
            "scheduled": scheduled.isoformat() if scheduled else None,
        }

        if self.native_value is not None:
            attrs["last_cleaned_local"] = dt_util.as_local(self.native_value).isoformat()

        return attrs


class VacuumRoomNextPlannedSensor(SensorEntity):
    """Timestamp sensor for the next planned room/mode cleaning."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_has_entity_name = True

    def __init__(
        self,
        manager: VacuumRoomSchedulerManager,
        room_name: str,
        mode: str,
    ) -> None:
        """Initialize the sensor."""
        self._manager = manager
        self._room_name = room_name
        self._mode = mode
        self._unsub_manager: Callable[[], None] | None = None

        mode_label = "vacuum" if mode == CLEAN_MODE_VACUUM else "mop"
        self._attr_name = f"{room_name} next {mode_label} due"
        self._attr_unique_id = (
            f"{manager.entry.entry_id}_{room_name}_{mode}_next_planned"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, manager.entry.entry_id)},
            name="Vacuum Room Scheduler",
            manufacturer="nbross",
            model="room scheduler",
        )

    async def async_added_to_hass(self) -> None:
        """Register for manager updates."""
        self._unsub_manager = self._manager.register_state_listener(self._handle_update)
        self.async_on_remove(self._remove_manager_listener)
        self.async_write_ha_state()

    @callback
    def _handle_update(self) -> None:
        """Update the sensor when the manager changes."""
        self.async_write_ha_state()

    @callback
    def _remove_manager_listener(self) -> None:
        """Remove the manager listener if present."""
        if self._unsub_manager is not None:
            self._unsub_manager()
            self._unsub_manager = None

    @property
    def native_value(self) -> datetime | None:
        """Return the next planned cleaning timestamp."""
        return self._manager.get_next_planned(self._room_name, self._mode)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose supporting room metadata."""
        scheduled = self._manager.get_scheduled(self._room_name, self._mode)
        last_cleaned = self._manager.get_last_cleaned(self._room_name, self._mode)
        next_due = self._manager.get_next_planned(self._room_name, self._mode)
        interval_days = self._manager.get_interval_days(self._mode)

        attrs: dict[str, Any] = {
            "room": self._room_name,
            "mode": self._mode,
            "segment_id": self._manager.get_room_segment_id(self._room_name),
            "area_id": self._manager.get_room_area_id(self._room_name),
            "scheduled": scheduled.isoformat() if scheduled else None,
            "next_due": next_due.isoformat() if next_due else None,
            "interval_days": interval_days,
            "planning_source": "scheduled" if scheduled else ("due" if last_cleaned else "unknown"),
        }

        if self.native_value is not None:
            attrs["next_planned_local"] = dt_util.as_local(self.native_value).isoformat()

        return attrs
