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

    # Welcome notification on first install (no suggestions yet = fresh install)
    if not coordinator.suggestions and not coordinator.last_scan:
        hass.components.persistent_notification.async_create(
            title="HA Rhythm installed!",
            message=(
                "HA Rhythm is ready to learn your home's routines.\n\n"
                "**Step 1 — Run your first scan:**\n"
                "Go to **Developer Tools → Actions**, search for `ha_rhythm.scan` "
                "and press **Perform action**. The scan takes 1–3 minutes.\n\n"
                "**Step 2 — Review suggestions:**\n"
                "After the scan you'll get a notification listing what was found. "
                "Each suggestion contains a ready-to-use automation you can deploy "
                "with one click.\n\n"
                "**Step 3 — Add the dashboard card (optional):**\n"
                "See the README for a copy-paste Lovelace card that gives you "
                "scan + review + deploy in one place."
            ),
            notification_id="rhythm_welcome",
        )

    # ── Services ──────────────────────────────────────────────────────────────

    async def handle_scan(call: ServiceCall) -> None:
        count = await coordinator.async_scan()
        _LOGGER.info("HA Rhythm: scan complete — %d new suggestions", count)
        if count == 0:
            hass.components.persistent_notification.async_create(
                title="HA Rhythm — scan complete",
                message=(
                    "No new patterns found this time.\n\n"
                    "This is normal if:\n"
                    "- You have less than 7 days of history\n"
                    "- Your devices don't follow a regular schedule yet\n\n"
                    "Try again after a few more days of normal use."
                ),
                notification_id="rhythm_scan_done",
            )
        else:
            pending = coordinator.pending_suggestions
            lines = "\n".join(
                f"• **{s['friendly_name']}** — {s['explanation']} "
                f"(ID: `{s['id']}`)"
                for s in pending[:10]
            )
            hass.components.persistent_notification.async_create(
                title=f"HA Rhythm — {count} new suggestion{'s' if count != 1 else ''}",
                message=(
                    f"Found {count} automation suggestion{'s' if count != 1 else ''} "
                    f"based on your behavior:\n\n"
                    f"{lines}\n\n"
                    f"**To deploy** a suggestion, go to "
                    f"**Developer Tools → Actions → ha_rhythm.deploy** "
                    f"and enter the suggestion ID shown above.\n\n"
                    f"Or add the dashboard card from the README for an easier review flow."
                ),
                notification_id="rhythm_scan_done",
            )

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
