"""Analyze paired, manually collected mobile app latency observations.

``analyze`` accepts dictionaries such as rows read by ``csv.DictReader``.
Comparison groups fix the scenario, stock code, keyboard state, capture and
timing methods, start state, market phase, visible completion criterion, frame
rate, device, and network.
Each run ID must contain one Toss row and one KIS row. Only completed pairs
contribute latency statistics; failures remain visible.

Optional timing fields have deliberately narrow meanings:

* ``first_response_ms``: action to first observed response.
* ``content_ms``: first response to visible content completion.
* ``unattributed_ms``: elapsed - first_response - content.  This is *not* a
  rendering estimate; it can include scheduling, client work, and measurement
  error.

The module reports associations in these observations, never a protocol or
server-side causal explanation.  It does not attempt to inspect app traffic.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
import hashlib
import json
import math
import random
import re
from statistics import median
from typing import Any

from . import db


_REQUIRED_TEXT = ("app", "scenario", "run_id")
_OPTIONAL_TEXT = ("device", "network_type", "protocol_observation", "timestamp", "app_version")
_OPTIONAL_NUMBERS = (
    "first_response_ms",
    "content_ms",
    "frame_jank_pct",
    "network_rtt_ms",
)
_METRIC_UNITS = {
    "elapsed_ms": "ms",
    "first_response_ms": "ms",
    "content_ms": "ms",
    "unattributed_ms": "ms",
    "frame_jank_pct": "%",
    "network_rtt_ms": "ms",
}
_BOOTSTRAP_REPLICATES = 2000
_MIN_BOOTSTRAP_PAIRS = 5
_MIN_P95_PAIRS = 20
_GROUP_FIELDS = (
    "scenario", "stock_code", "keyboard_state", "capture_method", "timing_basis", "start_state", "market_phase",
    "completion_criterion", "capture_fps", "device", "network_type",
)


def _text(value: object, *, field: str, index: int, required: bool) -> str | None:
    if value is None or value == "":
        if required:
            raise ValueError(f"row {index}: {field} is required")
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"row {index}: {field} must be text")
    result = str(value).strip()
    if not result:
        if required:
            raise ValueError(f"row {index}: {field} is required")
        return None
    return result


def _number(
    value: object, *, field: str, index: int, required: bool = False
) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"row {index}: {field} is required")
        return None
    if isinstance(value, bool):
        raise ValueError(f"row {index}: {field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"row {index}: {field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"row {index}: {field} must be a finite number")
    if result < 0 or (field == "elapsed_ms" and result == 0):
        boundary = "positive" if field == "elapsed_ms" else "nonnegative"
        raise ValueError(f"row {index}: {field} must be {boundary}")
    if field == "frame_jank_pct" and result > 100:
        raise ValueError(f"row {index}: frame_jank_pct must be at most 100")
    return result


def _normalize(row: Mapping[str, object], index: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for field in _REQUIRED_TEXT:
        result[field] = _text(row.get(field), field=field, index=index, required=True)
    result["app"] = result["app"].lower()
    if result["app"] not in {"toss", "kis"}:
        raise ValueError(f"row {index}: app must be 'toss' or 'kis'")
    result["stock_code"] = _text(
        row.get("stock_code"), field="stock_code", index=index, required=False
    ) or "unspecified"
    if result["stock_code"] not in {"unspecified", "not_applicable"} and not re.fullmatch(r"[0-9]{6}", result["stock_code"]):
        raise ValueError(f"row {index}: stock_code must be six digits, not_applicable, or unspecified")
    result["keyboard_state"] = _text(
        row.get("keyboard_state"), field="keyboard_state", index=index, required=False
    ) or "unspecified"
    if result["keyboard_state"] not in {"shown", "hidden", "not_applicable", "unspecified"}:
        raise ValueError(f"row {index}: keyboard_state is invalid")
    result["elapsed_ms"] = _number(row.get("elapsed_ms"), field="elapsed_ms", index=index)
    for field in _OPTIONAL_TEXT:
        result[field] = _text(row.get(field), field=field, index=index, required=False)
    if result["app_version"] is not None and len(result["app_version"]) > 80:
        raise ValueError(f"row {index}: app_version exceeds 80 characters")
    result["capture_method"] = _text(
        row.get("capture_method"), field="capture_method", index=index, required=False
    ) or "unspecified"
    if result["capture_method"] not in {"external_camera", "screen_recording", "manual_other", "unspecified"}:
        raise ValueError(f"row {index}: capture_method is invalid")
    result["timing_basis"] = _text(
        row.get("timing_basis"), field="timing_basis", index=index, required=False
    ) or "unspecified"
    for field in ("start_state", "market_phase", "completion_criterion"):
        result[field] = _text(row.get(field), field=field, index=index, required=False) or "unspecified"
    if result["start_state"] not in {"warm", "cold", "unspecified"}:
        raise ValueError(f"row {index}: start_state is invalid")
    if result["market_phase"] not in {"open", "closed", "unspecified"}:
        raise ValueError(f"row {index}: market_phase is invalid")
    if len(result["completion_criterion"]) > 160:
        raise ValueError(f"row {index}: completion_criterion exceeds 160 characters")
    result["status"] = _text(row.get("status"), field="status", index=index, required=False) or "completed"
    if result["status"] not in {"completed", "timeout", "error"}:
        raise ValueError(f"row {index}: status must be completed, timeout, or error")
    result["capture_fps"] = _number(row.get("capture_fps"), field="capture_fps", index=index)
    if result["capture_fps"] is not None and not 1 <= result["capture_fps"] <= 1000:
        raise ValueError(f"row {index}: capture_fps must be between 1 and 1000")
    order = _number(row.get("run_order"), field="run_order", index=index)
    if order is not None and (not order.is_integer() or order not in {1, 2, 3, 4}):
        raise ValueError(f"row {index}: run_order must be an integer from 1 to 4")
    result["run_order"] = int(order) if order is not None else None
    for field in ("start_frame", "end_frame"):
        value = _number(row.get(field), field=field, index=index)
        if value is not None and (not value.is_integer() or value > 1_000_000_000):
            raise ValueError(f"row {index}: {field} must be an integer from 0 to 1000000000")
        result[field] = int(value) if value is not None else None
    for field in ("start_pts_ms", "end_pts_ms"):
        result[field] = _number(row.get(field), field=field, index=index)
        if result[field] is not None and result[field] > 1_000_000_000_000:
            raise ValueError(f"row {index}: {field} must be at most 1000000000000")
    try:
        result["elapsed_ms"] = db.validate_timing(result)
    except ValueError as exc:
        raise ValueError(f"row {index}: {exc}") from exc
    for field in _OPTIONAL_NUMBERS:
        result[field] = _number(row.get(field), field=field, index=index)

    stamp = result["timestamp"]
    if stamp is not None:
        try:
            datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"row {index}: timestamp must be ISO 8601") from exc

    first = result["first_response_ms"]
    content = result["content_ms"]
    elapsed = result["elapsed_ms"]
    if first is not None and first > elapsed:
        raise ValueError(f"row {index}: first_response_ms exceeds elapsed_ms")
    if content is not None and content > elapsed:
        raise ValueError(f"row {index}: content_ms exceeds elapsed_ms")
    if first is not None and content is not None:
        if first + content > elapsed + 1e-9:
            raise ValueError(
                f"row {index}: first_response_ms + content_ms exceeds elapsed_ms"
            )
        result["unattributed_ms"] = max(0.0, elapsed - first - content)
    else:
        result["unattributed_ms"] = None
    return result


def _percentile(values: list[float], percentile: float) -> float:
    """Linear interpolation of the sorted sample, including both endpoints."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lo = math.floor(position)
    hi = math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def _distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "median": median(values),
        "p95": _percentile(values, 0.95) if len(values) >= _MIN_P95_PAIRS else None,
    }


def _bootstrap_median_ci(differences: list[float]) -> list[float] | None:
    if len(differences) < _MIN_BOOTSTRAP_PAIRS:
        return None
    # Input-derived seed makes results stable across processes and Python runs.
    seed_data = ",".join(d.hex() for d in differences).encode("ascii")
    seed = int.from_bytes(hashlib.sha256(seed_data).digest()[:8], "big")
    rng = random.Random(seed)
    n = len(differences)
    samples = [
        median(differences[rng.randrange(n)] for _ in range(n))
        for _ in range(_BOOTSTRAP_REPLICATES)
    ]
    return [_percentile(samples, 0.025), _percentile(samples, 0.975)]


def _metric(pairs: list[tuple[dict[str, Any], dict[str, Any]]], key: str) -> dict[str, Any] | None:
    complete = [(t[key], k[key]) for t, k in pairs if t[key] is not None and k[key] is not None]
    if not complete:
        return None
    toss_values = [t for t, _ in complete]
    kis_values = [k for _, k in complete]
    differences = [t - k for t, k in complete]
    result: dict[str, Any] = {
        "unit": _METRIC_UNITS[key],
        "paired_runs": len(complete),
        "toss": _distribution(toss_values),
        "kis": _distribution(kis_values),
        "toss_minus_kis": {
            "median": median(differences),
            "bootstrap_95_ci": _bootstrap_median_ci(differences),
            "toss_lower_runs": sum(d < 0 for d in differences),
            "equal_runs": sum(d == 0 for d in differences),
            "kis_lower_runs": sum(d > 0 for d in differences),
        },
    }
    if key == "elapsed_ms":
        result["toss_minus_kis"]["median_relative_pct"] = median(
            (t - k) / k * 100 for t, k in complete
        )
    return result


def _quality_checks(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]], group_fields: dict[str, Any]
) -> dict[str, Any]:
    """Summarize recording consistency without treating small counts as a trend."""
    app_versions: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    for app, position in (("toss", 0), ("kis", 1)):
        version_counts: dict[str, int] = defaultdict(int)
        missing = 0
        for pair in pairs:
            version = pair[position]["app_version"]
            if version is None:
                missing += 1
            else:
                version_counts[version] += 1
        mixed = len(version_counts) >= 2
        app_versions[app] = {
            "counts": dict(sorted(version_counts.items())),
            "missing": missing,
            "mixed": mixed,
        }
        if mixed:
            warnings.append(f"mixed_{app}_versions")

    order_counts = {"toss_first": 0, "kis_first": 0, "unrecorded": 0, "duplicate": 0}
    duplicate_run_ids = []
    for toss, kis in pairs:
        toss_order, kis_order = toss["run_order"], kis["run_order"]
        if toss_order is None or kis_order is None:
            order_counts["unrecorded"] += 1
        elif toss_order == kis_order:
            order_counts["duplicate"] += 1
            duplicate_run_ids.append(toss["run_id"])
        elif toss_order < kis_order:
            order_counts["toss_first"] += 1
        else:
            order_counts["kis_first"] += 1
    if order_counts["unrecorded"]:
        warnings.append("missing_run_order")
    if duplicate_run_ids:
        warnings.append("duplicate_run_order")
    condition_completeness = {
        "stock_code_recorded": group_fields["stock_code"] != "unspecified",
        "keyboard_state_recorded": group_fields["keyboard_state"] != "unspecified",
    }
    if pairs and not condition_completeness["stock_code_recorded"]:
        warnings.append("missing_stock_code")
    if pairs and not condition_completeness["keyboard_state_recorded"]:
        warnings.append("missing_keyboard_state")
    return {
        "app_versions": app_versions,
        "run_order": {**order_counts, "duplicate_run_ids": duplicate_run_ids},
        "condition_completeness": condition_completeness,
        "warnings": warnings,
    }


def analyze(rows: Iterable[Mapping[str, object]]) -> dict[str, Any]:
    """Validate and summarize matched Toss/KIS measurements.

    Invalid rows raise ``ValueError`` with a one-based row number.  Ambiguous,
    incomplete, and condition-mismatched run groups are listed as exclusions.
    All distributions and differences use only complete pairs.  Negative
    ``toss_minus_kis`` latency values mean Toss was faster in this sample.
    The returned object contains only JSON-serializable primitives.
    """
    if isinstance(rows, (str, bytes, Mapping)):
        raise ValueError("rows must be an iterable of row dictionaries")
    try:
        iterator = iter(rows)
    except TypeError as exc:
        raise ValueError("rows must be an iterable of row dictionaries") from exc

    normalized: list[dict[str, Any]] = []
    for index, row in enumerate(iterator, start=1):
        if not isinstance(row, Mapping):
            raise ValueError(f"row {index}: expected a dictionary")
        normalized.append(_normalize(row, index))

    by_group: dict[tuple[Any, ...], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in normalized:
        by_group[tuple(row[field] for field in _GROUP_FIELDS)][row["run_id"]].append(row)

    scenarios: dict[str, Any] = {}
    total_pairs = 0
    for group_values in sorted(by_group, key=lambda values: tuple(str(value) for value in values)):
        groups = by_group[group_values]
        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        exclusions: list[dict[str, Any]] = []
        status_counts = {
            app: {status: 0 for status in ("completed", "timeout", "error")}
            for app in ("toss", "kis")
        }
        for rows_in_run in groups.values():
            for row in rows_in_run:
                status_counts[row["app"]][row["status"]] += 1
        for run_id in sorted(groups):
            group = groups[run_id]
            apps = {row["app"] for row in group}
            if len(group) == 1:
                reason = "missing_counterpart"
            elif len(group) != 2 or apps != {"toss", "kis"}:
                reason = "duplicate_or_ambiguous"
            elif len({(r["device"], r["network_type"]) for r in group}) != 1:
                reason = "condition_mismatch"
            elif any(row["status"] != "completed" for row in group):
                reason = "noncompleted"
            else:
                toss = next(r for r in group if r["app"] == "toss")
                kis = next(r for r in group if r["app"] == "kis")
                pairs.append((toss, kis))
                continue
            exclusions.append(
                {
                    "run_id": run_id,
                    "reason": reason,
                    "rows": len(group),
                    "apps": sorted(row["app"] for row in group),
                    "statuses": sorted(row["status"] for row in group),
                }
            )

        metric_results = {
            key: summary
            for key in _METRIC_UNITS
            if (summary := _metric(pairs, key)) is not None
        }
        protocol_observations = {
            app: sorted(
                {
                    row["protocol_observation"]
                    for pair in pairs
                    for row in pair
                    if row["app"] == app and row["protocol_observation"] is not None
                }
            )
            for app in ("toss", "kis")
        }
        group_fields = dict(zip(_GROUP_FIELDS, group_values))
        key = json.dumps(group_values, ensure_ascii=False, separators=(",", ":"))
        scenarios[key] = {
            **group_fields,
            "input_rows": sum(map(len, groups.values())),
            "paired_runs": len(pairs),
            "status_counts": status_counts,
            "excluded_groups": exclusions,
            "metrics": metric_results,
            "quality_checks": _quality_checks(pairs, group_fields),
            "entered_protocol_observations": protocol_observations,
        }
        total_pairs += len(pairs)

    return {
        "schema_version": 6,
        "input_rows": len(normalized),
        "scenario_count": len({item["scenario"] for item in scenarios.values()}),
        "analysis_group_count": len(scenarios),
        "total_paired_runs": total_pairs,
        "noncompleted_observations": sum(row["status"] != "completed" for row in normalized),
        "scenarios": scenarios,
        "interpretation": {
            "difference_sign": "Toss minus KIS; negative means lower Toss value",
            "bootstrap_ci": (
                "Exploratory paired percentile bootstrap of median difference; "
                f"shown only with at least {_MIN_BOOTSTRAP_PAIRS} pairs"
            ),
            "p95": f"Shown only with at least {_MIN_P95_PAIRS} completed pairs",
            "unattributed_ms": (
                "Remainder of elapsed time; not a direct rendering measurement"
            ),
            "causality": (
                "Observed UI latency and entered protocol labels do not establish "
                "which transport, server, or client behavior caused a difference"
            ),
        },
    }
