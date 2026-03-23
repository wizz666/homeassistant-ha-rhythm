"""HA Rhythm — passive behavioral pattern detection and automation suggestions."""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN
from .coordinator import HaRhythmCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    coordinator = HaRhythmCoordinator(hass, entry)
    await coordinator.async_load()
    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    # ── Services ──────────────────────────────────────────────────────────────

    async def handle_scan(call: ServiceCall) -> None:
        count = await coordinator.async_scan()
        _LOGGER.info("HA Rhythm: scan complete — %d new suggestions", count)

    async def handle_deploy(call: ServiceCall) -> None:
        await coordinator.async_deploy(call.data["suggestion_id"])

    async def handle_dismiss(call: ServiceCall) -> None:
        await coordinator.async_dismiss(call.data["suggestion_id"])

    async def handle_feedback(call: ServiceCall) -> None:
        await coordinator.async_feedback(
            call.data["suggestion_id"], call.data["rating"]
        )

    async def handle_delete(call: ServiceCall) -> None:
        await coordinator.async_delete(call.data["suggestion_id"])

    hass.services.async_register(DOMAIN, "scan", handle_scan)

    hass.services.async_register(
        DOMAIN, "deploy", handle_deploy,
        schema=vol.Schema({vol.Required("suggestion_id"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN, "dismiss", handle_dismiss,
        schema=vol.Schema({vol.Required("suggestion_id"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN, "feedback", handle_feedback,
        schema=vol.Schema({
            vol.Required("suggestion_id"): cv.string,
            vol.Required("rating"): vol.In(["good", "bad"]),
        }),
    )
    hass.services.async_register(
        DOMAIN, "delete", handle_delete,
        schema=vol.Schema({vol.Required("suggestion_id"): cv.string}),
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        for service in ("scan", "deploy", "dismiss", "feedback", "delete"):
            hass.services.async_remove(DOMAIN, service)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
