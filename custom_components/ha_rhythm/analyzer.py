"""HA Rhythm — pattern analyzer.

Pure Python, no ML libraries. Reads HA recorder SQLite database
and extracts recurring behavioral patterns from state history.
"""
from __future__ import annotations

import sqlite3
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean, stdev

_LOGGER = logging.getLogger(__name__)

# Domains worth analyzing for behavioral patterns
BEHAVIORAL_DOMAINS = (
    "light", "switch", "media_player", "person",
    "cover", "climate", "input_boolean", "fan",
)

# States that count as "active" per domain
ACTIVE_STATES: dict[str, tuple[str, ...]] = {
    "light": ("on",),
    "switch": ("on",),
    "media_player": ("playing", "on", "paused"),
    "person": ("home",),
    "cover": ("open",),
    "climate": ("heat", "cool", "heat_cool", "auto", "fan_only", "dry"),
    "input_boolean": ("on",),
    "fan": ("on",),
}

# Minimum / maximum daily activations for a "behavioral" entity
MIN_DAILY_ACTIVATIONS = 0.3   # at least once every 3 days on average
MAX_DAILY_ACTIVATIONS = 40.0  # not a noisy sensor

MIN_DAYS_FOR_PATTERN = 7      # need at least a week of data
MIN_CONSISTENCY = 0.45        # 45% of observed days must match
BUCKET_MINUTES = 15           # 96 buckets per day
BUCKETS_PER_DAY = 24 * 60 // BUCKET_MINUTES  # 96
CORRELATION_WINDOW_SEC = 300  # 5 minutes for correlation detection
MIN_CORRELATION = 0.65


@dataclass
class TimePattern:
    """A recurring time-based activation pattern for one entity."""
    entity_id: str
    friendly_name: str
    domain: str
    window_start: str          # "HH:MM"
    window_end: str            # "HH:MM"
    consistency: float         # 0.0-1.0
    days_observed: int
    weekday_only: bool
    weekend_only: bool
    typical_times: list[str]   # raw sample times
    correlated_with: list[dict] = field(default_factory=list)


@dataclass
class CorrelationPattern:
    """Entity B reliably activates after entity A."""
    trigger_entity: str
    trigger_friendly: str
    result_entity: str
    result_friendly: str
    correlation: float
    avg_lag_seconds: float
    days_observed: int


def _bucket(ts: float) -> int:
    """Convert unix timestamp to 15-min bucket index (0-95)."""
    dt = datetime.fromtimestamp(ts)
    return dt.hour * 4 + dt.minute // BUCKET_MINUTES


def _bucket_to_time(b: int) -> str:
    h = b // 4
    m = (b % 4) * BUCKET_MINUTES
    return f"{h:02d}:{m:02d}"


def _is_active(domain: str, state: str) -> bool:
    return state in ACTIVE_STATES.get(domain, ("on",))


def _load_events(
    db_path: Path,
    days: int = 30,
    domains: tuple[str, ...] = BEHAVIORAL_DOMAINS,
) -> dict[str, list[tuple[float, str]]]:
    """
    Returns {entity_id: [(timestamp, state), ...]} sorted by time.
    Handles both new (states_meta) and old recorder schemas.
    """
    cutoff = (datetime.now() - timedelta(days=days)).timestamp()
    result: dict[str, list[tuple[float, str]]] = defaultdict(list)

    domain_patterns = tuple(f"{d}.%" for d in domains)
    placeholders = ",".join("?" * len(domain_patterns))

    try:
        conn = sqlite3.connect(
            f"file:{db_path}?mode=ro",
            uri=True,
            timeout=10,
            check_same_thread=False,
        )
        conn.execute("PRAGMA journal_mode=WAL")
        cur = conn.cursor()

        # Try new schema (HA 2022.4+) with states_meta
        try:
            cur.execute("SELECT 1 FROM states_meta LIMIT 1")
            # New schema
            like_clause = " OR ".join(f"sm.entity_id LIKE ?" for _ in domain_patterns)
            query = f"""
                SELECT sm.entity_id, s.state, s.last_changed_ts
                FROM states s
                JOIN states_meta sm ON s.metadata_id = sm.metadata_id
                WHERE ({like_clause})
                  AND s.last_changed_ts > ?
                  AND s.state NOT IN ('unavailable', 'unknown', '')
                ORDER BY s.last_changed_ts
            """
            cur.execute(query, (*domain_patterns, cutoff))
        except sqlite3.OperationalError:
            # Old schema — entity_id directly on states table
            like_clause = " OR ".join(f"entity_id LIKE ?" for _ in domain_patterns)
            query = f"""
                SELECT entity_id, state, last_changed
                FROM states
                WHERE ({like_clause})
                  AND last_changed > ?
                  AND state NOT IN ('unavailable', 'unknown', '')
                ORDER BY last_changed
            """
            cutoff_str = datetime.fromtimestamp(cutoff).isoformat()
            cur.execute(query, (*domain_patterns, cutoff_str))

        for entity_id, state, ts in cur.fetchall():
            # Normalize timestamp to float
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts).timestamp()
                except ValueError:
                    continue
            result[entity_id].append((float(ts), state))

        conn.close()
    except Exception as e:
        _LOGGER.error("HA Rhythm: failed to read recorder DB: %s", e)

    return dict(result)


def _get_friendly_names(db_path: Path) -> dict[str, str]:
    """Extract friendly names from the most recent attributes JSON."""
    import json
    names: dict[str, str] = {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT sm.entity_id, s.attributes
                FROM states s
                JOIN states_meta sm ON s.metadata_id = sm.metadata_id
                WHERE s.attributes IS NOT NULL
                  AND s.attributes != 'null'
                GROUP BY sm.entity_id
                HAVING MAX(s.state_id)
            """)
        except sqlite3.OperationalError:
            cur.execute("""
                SELECT entity_id, attributes
                FROM states
                WHERE attributes IS NOT NULL
                GROUP BY entity_id
                HAVING MAX(state_id)
            """)
        for entity_id, attrs_str in cur.fetchall():
            try:
                attrs = json.loads(attrs_str)
                fn = attrs.get("friendly_name")
                if fn:
                    names[entity_id] = fn
            except Exception:
                pass
        conn.close()
    except Exception:
        pass
    return names


def analyze_patterns(
    db_path: Path,
    days: int = 30,
    min_consistency: float = MIN_CONSISTENCY,
) -> tuple[list[TimePattern], list[CorrelationPattern]]:
    """
    Main entry point. Returns (time_patterns, correlation_patterns).
    Runs synchronously — call via run_in_executor.
    """
    _LOGGER.info("HA Rhythm: starting analysis on %d days of data", days)

    events = _load_events(db_path, days=days)
    friendly_names = _get_friendly_names(db_path)

    if not events:
        _LOGGER.warning("HA Rhythm: no events loaded from recorder")
        return [], []

    time_patterns: list[TimePattern] = []
    all_activations: dict[str, list[float]] = {}  # for correlation

    # ── Time pattern detection ────────────────────────────────────────────────
    for entity_id, ev_list in events.items():
        domain = entity_id.split(".")[0]
        if domain not in BEHAVIORAL_DOMAINS:
            continue

        # Collect timestamps of "becoming active" (off → on transitions)
        activations: list[float] = []
        prev_state = None
        all_states = [s for _, s in ev_list]
        only_active = all_states and all(
            _is_active(domain, s) for s in all_states
        )

        if only_active:
            # Recorder has no "off" states — use first daily occurrence instead
            seen_days: set = set()
            for ts, state in ev_list:
                day = datetime.fromtimestamp(ts).date()
                if day not in seen_days:
                    activations.append(ts)
                    seen_days.add(day)
        else:
            for ts, state in ev_list:
                if _is_active(domain, state) and not _is_active(domain, prev_state or ""):
                    activations.append(ts)
                prev_state = state

        if not activations:
            continue

        total_days = days
        daily_rate = len(activations) / total_days
        if daily_rate < MIN_DAILY_ACTIVATIONS or daily_rate > MAX_DAILY_ACTIVATIONS:
            continue

        all_activations[entity_id] = activations

        # Check we have enough days with data
        active_days_set = {
            datetime.fromtimestamp(ts).date() for ts in activations
        }
        n_days = len(active_days_set)
        if n_days < MIN_DAYS_FOR_PATTERN:
            continue

        # Build 15-min bucket histogram
        # Separate weekday vs weekend
        wd_buckets = defaultdict(int)
        we_buckets = defaultdict(int)
        wd_days: set = set()
        we_days: set = set()

        for ts in activations:
            dt = datetime.fromtimestamp(ts)
            b = _bucket(ts)
            if dt.weekday() < 5:
                wd_buckets[b] += 1
                wd_days.add(dt.date())
            else:
                we_buckets[b] += 1
                we_days.add(dt.date())

        # Find strongest pattern across all days first
        all_buckets = defaultdict(int)
        all_days: set = set()
        for ts in activations:
            dt = datetime.fromtimestamp(ts)
            all_buckets[_bucket(ts)] += 1
            all_days.add(dt.date())

        n_all = len(all_days)

        # Find peak bucket and expand to window
        if not all_buckets:
            continue

        peak_bucket = max(all_buckets, key=lambda b: all_buckets[b])
        peak_consistency = all_buckets[peak_bucket] / n_all

        if peak_consistency < min_consistency:
            continue

        # Expand window: include adjacent buckets above 40% threshold
        threshold = max(0.4, min_consistency * 0.7)
        window_buckets = [peak_bucket]
        # Expand left
        b = (peak_bucket - 1) % BUCKETS_PER_DAY
        while all_buckets[b] / n_all >= threshold and b != peak_bucket:
            window_buckets.insert(0, b)
            b = (b - 1) % BUCKETS_PER_DAY
        # Expand right
        b = (peak_bucket + 1) % BUCKETS_PER_DAY
        while all_buckets[b] / n_all >= threshold and b != peak_bucket:
            window_buckets.append(b)
            b = (b + 1) % BUCKETS_PER_DAY

        window_buckets = sorted(set(window_buckets))
        window_start = _bucket_to_time(window_buckets[0])
        window_end_b = (window_buckets[-1] + 1) % BUCKETS_PER_DAY
        window_end = _bucket_to_time(window_end_b)

        # Weekday vs weekend analysis
        wd_count = len(wd_days)
        we_count = len(we_days)
        weekday_only = wd_count >= 3 and we_count == 0
        weekend_only = we_count >= 2 and wd_count == 0

        # Sample of actual times for LLM context
        sample_times = []
        for ts in activations[-10:]:
            dt = datetime.fromtimestamp(ts)
            if _bucket(ts) in window_buckets:
                sample_times.append(dt.strftime("%H:%M"))

        friendly = friendly_names.get(entity_id, entity_id)

        pattern = TimePattern(
            entity_id=entity_id,
            friendly_name=friendly,
            domain=domain,
            window_start=window_start,
            window_end=window_end,
            consistency=round(peak_consistency, 2),
            days_observed=n_all,
            weekday_only=weekday_only,
            weekend_only=weekend_only,
            typical_times=sample_times[:8],
        )
        time_patterns.append(pattern)
        _LOGGER.debug("HA Rhythm: pattern found — %s @ %s-%s (%.0f%%)",
                      entity_id, window_start, window_end, peak_consistency * 100)

    # ── Correlation detection ─────────────────────────────────────────────────
    correlation_patterns: list[CorrelationPattern] = []
    entity_ids = list(all_activations.keys())

    for i, trigger_id in enumerate(entity_ids):
        trigger_acts = sorted(all_activations[trigger_id])
        for result_id in entity_ids:
            if result_id == trigger_id:
                continue
            result_acts = sorted(all_activations[result_id])
            if not result_acts:
                continue

            hits = 0
            lags: list[float] = []
            for t_ts in trigger_acts:
                # Look for result activation within the correlation window
                for r_ts in result_acts:
                    lag = r_ts - t_ts
                    if 2 <= lag <= CORRELATION_WINDOW_SEC:
                        hits += 1
                        lags.append(lag)
                        break

            if len(trigger_acts) < 5:
                continue
            corr = hits / len(trigger_acts)
            if corr < MIN_CORRELATION:
                continue

            avg_lag = mean(lags) if lags else 0
            corr_pattern = CorrelationPattern(
                trigger_entity=trigger_id,
                trigger_friendly=friendly_names.get(trigger_id, trigger_id),
                result_entity=result_id,
                result_friendly=friendly_names.get(result_id, result_id),
                correlation=round(corr, 2),
                avg_lag_seconds=round(avg_lag),
                days_observed=len({
                    datetime.fromtimestamp(ts).date()
                    for ts in trigger_acts
                }),
            )
            correlation_patterns.append(corr_pattern)
            _LOGGER.debug("HA Rhythm: correlation %s → %s (%.0f%%, lag %.0fs)",
                          trigger_id, result_id, corr * 100, avg_lag)

    # Attach correlations to time patterns
    corr_map = defaultdict(list)
    for cp in correlation_patterns:
        corr_map[cp.trigger_entity].append({
            "entity_id": cp.result_entity,
            "friendly_name": cp.result_friendly,
            "correlation": cp.correlation,
            "avg_lag_seconds": cp.avg_lag_seconds,
        })
    for tp in time_patterns:
        tp.correlated_with = corr_map.get(tp.entity_id, [])

    # Sort by consistency descending
    time_patterns.sort(key=lambda p: p.consistency, reverse=True)

    _LOGGER.info(
        "HA Rhythm: analysis complete — %d time patterns, %d correlations",
        len(time_patterns), len(correlation_patterns),
    )
    return time_patterns, correlation_patterns
