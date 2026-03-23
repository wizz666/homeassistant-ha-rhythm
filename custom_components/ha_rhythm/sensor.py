"""HA Rhythm sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import HaRhythmCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: HaRhythmCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        HaRhythmStatusSensor(coordinator, entry),
        HaRhythmPatternsSensor(coordinator, entry),
        HaRhythmPendingSensor(coordinator, entry),
        HaRhythmDeployedSensor(coordinator, entry),
    ])


class _HaRhythmBase(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: HaRhythmCoordinator, entry: ConfigEntry) -> None:
        self._coordinator = coordinator
        self._entry = entry
        coordinator.async_add_listener(self._on_update)

    def _on_update(self) -> None:
        self.schedule_update_ha_state()

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": "HA Rhythm",
            "manufacturer": "HA Rhythm",
            "model": "Behavioral Pattern Engine",
        }

    async def async_will_remove_from_hass(self) -> None:
        if self._on_update in self._coordinator._listeners:
            self._coordinator._listeners.remove(self._on_update)


class HaRhythmStatusSensor(_HaRhythmBase):
    _attr_icon = "mdi:brain"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_name = "Status"

    @property
    def native_value(self):
        return self._coordinator.status

    @property
    def extra_state_attributes(self):
        return {
            "last_scan": self._coordinator.last_scan,
            "patterns_found": len(self._coordinator.patterns),
            "pending_suggestions": len(self._coordinator.pending_suggestions),
            "deployed_suggestions": len(self._coordinator.deployed_suggestions),
        }


class HaRhythmPatternsSensor(_HaRhythmBase):
    _attr_icon = "mdi:chart-timeline-variant"
    _attr_native_unit_of_measurement = "patterns"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_patterns"
        self._attr_name = "Patterns Detected"

    @property
    def native_value(self):
        return len(self._coordinator.patterns)

    @property
    def extra_state_attributes(self):
        return {"patterns": self._coordinator.patterns}


class HaRhythmPendingSensor(_HaRhythmBase):
    _attr_icon = "mdi:lightbulb-on-outline"
    _attr_native_unit_of_measurement = "suggestions"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_pending"
        self._attr_name = "Pending Suggestions"

    @property
    def native_value(self):
        return len(self._coordinator.pending_suggestions)

    @property
    def extra_state_attributes(self):
        return {"suggestions": self._coordinator.pending_suggestions}


class HaRhythmDeployedSensor(_HaRhythmBase):
    _attr_icon = "mdi:check-circle-outline"
    _attr_native_unit_of_measurement = "automations"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_deployed"
        self._attr_name = "Deployed Automations"

    @property
    def native_value(self):
        return len(self._coordinator.deployed_suggestions)

    @property
    def extra_state_attributes(self):
        return {"automations": self._coordinator.deployed_suggestions}
