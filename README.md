# HA Rhythm 🎵

**Passive behavioral pattern detection for Home Assistant**

HA Rhythm silently observes how you actually use your home — lights, switches, media players, covers — and surfaces recurring patterns you didn't know existed. Then it uses AI to suggest ready-to-deploy automations based on what you *actually do*, not what you think you do.

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

---

## How it works

```
HA Rhythm observes your history:
  → Light in kitchen turns on at 07:15 every weekday (88% consistency)
  → TV switches on within 5 min of arriving home (74% of the time)
  → Bedroom fan starts every night around 22:30 (91% consistency)
                    ↓
AI generates automation suggestions:
  → "Turn on kitchen light at 07:15 on weekdays"
  → "Turn on TV when you arrive home"
  → "Start bedroom fan at 22:30 each night"

You review → Deploy → Done
```

No manual pattern writing. No guessing. Just your actual behavior, automated.

---

## Features

- **Zero configuration** — reads your existing HA recorder database, no extra sensors needed
- **Pure local analysis** — the pattern detection algorithm runs entirely on your HA instance, no data leaves your home
- **AI only for suggestions** — only the verified pattern facts are sent to your chosen AI provider
- **Weekday/weekend aware** — detects if a pattern only happens on workdays or weekends
- **Correlation detection** — finds "entity A triggers entity B" sequences
- **Full review flow** — nothing is deployed without your approval
- **Works with Groq** (free), **OpenRouter**, **Ollama**, **LM Studio**, **OpenAI**, **Anthropic**

---

## Installation

### HACS (recommended)
1. HACS → Integrations → ⋮ → Custom repositories
2. Add `wizz666/homeassistant-ha-rhythm` as type **Integration**
3. Install **HA Rhythm** and restart Home Assistant

### Manual
Copy `custom_components/ha_rhythm/` to your `config/custom_components/` directory and restart.

---

## One-time setup

Add this to your `configuration.yaml` so deployed automations take effect:

```yaml
automation rhythm: !include ha_rhythm_automations.yaml
```

Then restart Home Assistant once. After that, deploying suggestions only requires `automation.reload`.

---

## Usage

### 1. Run a scan

```yaml
service: ha_rhythm.scan
```

HA Rhythm analyzes your history (default: 21 days), detects patterns, and calls AI to generate suggestions. This may take 1–3 minutes depending on your history size and AI provider speed.

### 2. Review suggestions

Check `sensor.ha_rhythm_pending_suggestions` attributes — each suggestion includes:
- What pattern was detected (entity, time window, consistency %)
- AI explanation of the behavior
- The full automation YAML that will be deployed

### 3. Deploy

```yaml
service: ha_rhythm.deploy
data:
  suggestion_id: "a1b2c3d4"
```

### 4. Dismiss (not relevant)

```yaml
service: ha_rhythm.dismiss
data:
  suggestion_id: "a1b2c3d4"
```

### 5. Rate

```yaml
service: ha_rhythm.feedback
data:
  suggestion_id: "a1b2c3d4"
  rating: good  # or bad
```

---

## Services

| Service | Description |
|---|---|
| `ha_rhythm.scan` | Analyze history and generate suggestions |
| `ha_rhythm.deploy` | Deploy a pending suggestion |
| `ha_rhythm.dismiss` | Dismiss a suggestion without deploying |
| `ha_rhythm.feedback` | Rate a suggestion as `good` or `bad` |
| `ha_rhythm.delete` | Permanently delete a suggestion and its automation |

---

## Sensors

| Entity | Description |
|---|---|
| `sensor.ha_rhythm_status` | `idle` / `scanning` / `analyzing` / `error` |
| `sensor.ha_rhythm_patterns_detected` | Number of behavioral patterns found |
| `sensor.ha_rhythm_pending_suggestions` | Suggestions awaiting review |
| `sensor.ha_rhythm_deployed_automations` | Deployed and active automations |

---

## What patterns does it detect?

HA Rhythm looks for behavioral patterns in these domains:

- `light` — turning lights on/off
- `switch` — switch activations
- `media_player` — playing content
- `cover` — opening/closing blinds
- `climate` — changing heating/cooling mode
- `fan` — fan on/off
- `input_boolean` — virtual switches
- `person` — arriving home / leaving

A pattern must:
- Occur on at least **7 distinct days**
- Happen at least once every 3 days on average
- Show **55%+ consistency** in the same 15-minute time window

---

## Getting a free API key

| Provider | Where | Free? |
|---|---|---|
| **Groq** | [console.groq.com](https://console.groq.com) | ✅ Generous free tier |
| **OpenRouter** | [openrouter.ai/keys](https://openrouter.ai/keys) | ✅ Several free models |
| **Ollama** | Run locally | ✅ Fully local |

---

## Notes

- Suggestions are stored in `/config/.ha_rhythm_suggestions.json`
- Deployed automations are written to `/config/ha_rhythm_automations.yaml`
- The recorder database must exist (`home-assistant_v2.db`) — HA's default recorder creates this automatically
- More history = better patterns. 21 days is recommended; 7 is the minimum
