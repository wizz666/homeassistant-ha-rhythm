"""HA Rhythm coordinator — pipeline: analyze → LLM → suggestions."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .analyzer import TimePattern, analyze_patterns
from .const import (
    CONF_AI_KEY,
    CONF_AI_PROVIDER,
    CONF_AI_BASE_URL,
    CONF_AI_MODEL,
    CONF_SCAN_DAYS,
    DEFAULT_SCAN_DAYS,
    PROVIDER_PRESETS,
    PATTERN_SYSTEM_PROMPT,
)

_LOGGER = logging.getLogger(__name__)


class HaRhythmCoordinator:
    """Orchestrates the full Rhythm pipeline."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.status: str = "idle"
        self.patterns: list[dict] = []
        self.suggestions: list[dict] = []
        self.last_scan: str | None = None
        self._suggestions_file = Path(hass.config.path(".ha_rhythm_suggestions.json"))
        self._automations_file = Path(hass.config.path("ha_rhythm_automations.yaml"))
        self._listeners: list = []

    # ── Listeners ─────────────────────────────────────────────────────────────

    def async_add_listener(self, cb) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in self._listeners:
            cb()

    # ── Persistence ───────────────────────────────────────────────────────────

    async def async_load(self) -> None:
        try:
            text = await asyncio.get_event_loop().run_in_executor(
                None, self._suggestions_file.read_text
            )
            data = json.loads(text)
            self.suggestions = data.get("suggestions", [])
            self.last_scan = data.get("last_scan")
            self.patterns = data.get("patterns", [])
        except Exception:
            self.suggestions = []

    async def _save(self) -> None:
        data = json.dumps({
            "last_scan": self.last_scan,
            "patterns": self.patterns,
            "suggestions": self.suggestions,
        }, ensure_ascii=False, indent=2)
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: self._suggestions_file.write_text(data, encoding="utf-8")
        )

    # ── Provider ──────────────────────────────────────────────────────────────

    def _resolve_provider(self) -> tuple[str, str, str, str]:
        data = self.entry.data
        opts = self.entry.options
        provider = opts.get(CONF_AI_PROVIDER, data.get(CONF_AI_PROVIDER, "groq"))
        api_key = opts.get(CONF_AI_KEY, data.get(CONF_AI_KEY, ""))
        base_url = opts.get(CONF_AI_BASE_URL, data.get(CONF_AI_BASE_URL, ""))
        model = opts.get(CONF_AI_MODEL, data.get(CONF_AI_MODEL, ""))
        preset = PROVIDER_PRESETS.get(provider, {})
        if not base_url:
            base_url = preset.get("base_url", "")
        if not model:
            model = preset.get("model", "llama-3.3-70b-versatile")
        if not api_key and "api_key" in preset:
            api_key = preset["api_key"]
        return provider, api_key, base_url, model

    async def _call_llm(self, pattern_data: dict) -> dict:
        provider, api_key, base_url, model = self._resolve_provider()
        prompt = (
            f"Analyze this behavioral pattern and generate an automation:\n\n"
            f"{json.dumps(pattern_data, ensure_ascii=False, indent=2)}"
        )
        if provider == "anthropic":
            raw = await self._call_anthropic(api_key, prompt)
        else:
            raw = await self._call_openai_compat(api_key, prompt, base_url, model)
        return self._extract_json(raw)

    async def _call_openai_compat(
        self, api_key: str, prompt: str, base_url: str, model: str
    ) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": PATTERN_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 1200,
            "response_format": {"type": "json_object"},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                base_url.rstrip("/") + "/chat/completions",
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
        return data["choices"][0]["message"]["content"]

    async def _call_anthropic(self, api_key: str, prompt: str) -> str:
        headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        body = {
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 1200,
            "system": PATTERN_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json()
        return data["content"][0]["text"]

    @staticmethod
    def _extract_json(text) -> dict:
        if isinstance(text, dict):
            return text
        try:
            return json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"No JSON in response: {text[:200]}")

    # ── Main scan ─────────────────────────────────────────────────────────────

    async def async_scan(self) -> int:
        """Run full pipeline. Returns number of new suggestions generated."""
        self.status = "scanning"
        self._notify()

        scan_days = self.entry.options.get(
            CONF_SCAN_DAYS, self.entry.data.get(CONF_SCAN_DAYS, DEFAULT_SCAN_DAYS)
        )
        db_path = Path(self.hass.config.path("home-assistant_v2.db"))

        if not db_path.exists():
            _LOGGER.error("HA Rhythm: recorder database not found at %s", db_path)
            self.status = "error"
            self._notify()
            return 0

        # Run blocking analysis in executor
        try:
            time_patterns, _ = await asyncio.get_event_loop().run_in_executor(
                None, analyze_patterns, db_path, scan_days
            )
        except Exception as e:
            _LOGGER.error("HA Rhythm: analysis failed: %s", e)
            self.status = "error"
            self._notify()
            return 0

        if not time_patterns:
            _LOGGER.info("HA Rhythm: no patterns found (need more data?)")
            self.status = "idle"
            self.last_scan = datetime.now().strftime("%Y-%m-%d %H:%M")
            self._notify()
            return 0

        # Store raw patterns for sensor attributes
        self.patterns = [
            {
                "entity_id": p.entity_id,
                "friendly_name": p.friendly_name,
                "window": f"{p.window_start}–{p.window_end}",
                "consistency": p.consistency,
                "weekday_only": p.weekday_only,
                "weekend_only": p.weekend_only,
                "days_observed": p.days_observed,
            }
            for p in time_patterns
        ]

        self.status = "analyzing"
        self._notify()

        # Already-suggested entities (skip re-suggesting)
        existing_entities = {
            s["entity_id"]
            for s in self.suggestions
            if s["status"] in ("pending", "deployed")
        }

        new_count = 0
        for pattern in time_patterns[:12]:  # max 12 patterns per scan
            if pattern.entity_id in existing_entities:
                continue

            pattern_data = {
                "entity_id": pattern.entity_id,
                "friendly_name": pattern.friendly_name,
                "domain": pattern.domain,
                "window_start": pattern.window_start,
                "window_end": pattern.window_end,
                "consistency": pattern.consistency,
                "days_observed": pattern.days_observed,
                "weekday_only": pattern.weekday_only,
                "weekend_only": pattern.weekend_only,
                "typical_times": pattern.typical_times,
                "correlated_with": pattern.correlated_with[:3],
            }

            try:
                llm_result = await self._call_llm(pattern_data)
            except Exception as e:
                _LOGGER.warning("HA Rhythm: LLM call failed for %s: %s",
                                pattern.entity_id, e)
                continue

            if not llm_result.get("worth_automating", True):
                _LOGGER.debug("HA Rhythm: skipping %s — LLM says not worth automating",
                              pattern.entity_id)
                continue

            suggestion_id = str(uuid.uuid4())[:8]
            suggestion = {
                "id": suggestion_id,
                "entity_id": pattern.entity_id,
                "friendly_name": pattern.friendly_name,
                "pattern": pattern_data,
                "explanation": llm_result.get("explanation", ""),
                "confidence": llm_result.get("confidence", "medium"),
                "automation": llm_result.get("automation", {}),
                "status": "pending",
                "created_at": datetime.now().isoformat(),
                "deployed_at": None,
                "feedback": None,
            }
            self.suggestions.insert(0, suggestion)
            existing_entities.add(pattern.entity_id)
            new_count += 1
            _LOGGER.info("HA Rhythm: suggestion created for %s", pattern.entity_id)

        self.last_scan = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.status = "idle"
        await self._save()
        self._notify()

        _LOGGER.info("HA Rhythm: scan complete — %d new suggestions", new_count)
        return new_count

    # ── Deploy ────────────────────────────────────────────────────────────────

    async def async_deploy(self, suggestion_id: str) -> None:
        suggestion = self._get_suggestion(suggestion_id)
        if not suggestion:
            return

        await self._rewrite_automations_file()
        suggestion["status"] = "deployed"
        suggestion["deployed_at"] = datetime.now().isoformat()
        await self._save()

        await self.hass.services.async_call("automation", "reload")

        auto = suggestion.get("automation", {})
        self.hass.components.persistent_notification.async_create(
            title=f"HA Rhythm: '{auto.get('alias', suggestion['friendly_name'])}' deployed",
            message=(
                f"{suggestion['explanation']}\n\n"
                f"Automation added to `ha_rhythm_automations.yaml`.\n\n"
                f"ℹ️ Add `automation rhythm: !include ha_rhythm_automations.yaml` "
                f"to `configuration.yaml` if not already done."
            ),
            notification_id=f"rhythm_{suggestion_id}_deployed",
        )
        self._notify()

    async def _rewrite_automations_file(self) -> None:
        import yaml
        automations = []
        for s in self.suggestions:
            if s["status"] == "deployed" and s.get("automation"):
                auto = dict(s["automation"])
                auto["description"] = f"[Rhythm:{s['id']}] {auto.get('description', '')}"
                automations.append(auto)

        content = (
            "# HA Rhythm Automations — auto-generated from observed behavior\n"
            "# Do not edit manually — managed by the HA Rhythm integration\n\n"
        )
        if automations:
            content += yaml.dump(
                automations, allow_unicode=True,
                default_flow_style=False, sort_keys=False
            )
        else:
            content += "# No automations deployed yet\n"

        await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self._automations_file.write_text(content, encoding="utf-8"),
        )

    # ── Dismiss / Feedback / Delete ───────────────────────────────────────────

    async def async_dismiss(self, suggestion_id: str) -> None:
        s = self._get_suggestion(suggestion_id)
        if s:
            s["status"] = "dismissed"
            await self._save()
            self._notify()

    async def async_feedback(self, suggestion_id: str, rating: str) -> None:
        s = self._get_suggestion(suggestion_id)
        if s:
            s["feedback"] = rating
            await self._save()
            self._notify()

    async def async_delete(self, suggestion_id: str) -> None:
        self.suggestions = [s for s in self.suggestions if s["id"] != suggestion_id]
        await self._save()
        await self._rewrite_automations_file()
        await self.hass.services.async_call("automation", "reload")
        self._notify()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_suggestion(self, sid: str) -> dict | None:
        return next((s for s in self.suggestions if s["id"] == sid), None)

    @property
    def pending_suggestions(self) -> list[dict]:
        return [s for s in self.suggestions if s["status"] == "pending"]

    @property
    def deployed_suggestions(self) -> list[dict]:
        return [s for s in self.suggestions if s["status"] == "deployed"]
