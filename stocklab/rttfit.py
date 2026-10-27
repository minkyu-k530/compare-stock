"""Fit how visible latency grows when the network round trip is made longer.

An RTT session adds a known delay on the Mac hotspot (``tools/hotspot_delay.py``)
and repeats the same tap at every level, as planned in a ledger from
``tools/make_rtt_ledger.py``.  Regressing visible completion time on the added
round-trip time gives a slope ``k`` in milliseconds of screen time per
millisecond of RTT: the *effective* number of serial network round trips the
screen waited for.  Parallel requests count once; extra round trips from TCP
slow start, loss recovery, or retries count too, and a cached or prefetched
screen gives ``k`` near 0.

The slope describes how this screen behaved under this manipulation.  It does
not identify the protocol, the server design, or which requests formed the
chain; compare it with the serial depth from ``tools/analyze_pcap.py``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
import csv
import hashlib
import io
import math
import random
from statistics import median
from typing import Any

from .analysis import _GROUP_FIELDS, _normalize, _percentile


APPS = ("toss", "kis")
LEDGER_REQUIRED = ("block", "position", "run_id", "app", "added_rtt_ms", "outcome")
LEDGER_OUTCOMES = ("completed", "timeout", "error", "invalid_capture", "not_started")
MAX_ADDED_RTT_MS = 2000
_BOOTSTRAP_REPLICATES = 2000
_MIN_BOOTSTRAP_BLOCKS = 5
_MIN_PAIRS_PER_LEVEL = 5
#: Measured added RTT may differ from the plan by this much before a warning.
VERIFY_TOLERANCE_MS = 10.0
VERIFY_TOLERANCE_RATIO = 0.10
_CONDITION_FIELDS = tuple(field for field in _GROUP_FIELDS if field != "network_type")


def _ledger_number(raw: str | None, *, field: str, line: int, required: bool) -> float | None:
    text = (raw or "").strip()
    if not text:
        if required:
            raise ValueError(f"대장 {line}행: {field}가 비었습니다.")
        return None
    try:
        value = float(text)
    except ValueError as exc:
        raise ValueError(f"대장 {line}행: {field}는 숫자여야 합니다.") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"대장 {line}행: {field}는 0 이상의 유한한 숫자여야 합니다.")
    return value


def read_ledger(text: str) -> list[dict[str, Any]]:
    """Parse and check an RTT ledger; every attempt must have a final outcome."""
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    missing = [field for field in LEDGER_REQUIRED if field not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"RTT 대장에 필요한 열이 없습니다: {', '.join(missing)}")
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    block_levels: dict[int, float] = {}
    run_blocks: dict[str, int] = {}
    for line, raw in enumerate(reader, start=2):
        if None in raw:
            raise ValueError(f"대장 {line}행의 CSV 열 개수가 맞지 않습니다.")
        block = _ledger_number(raw["block"], field="block", line=line, required=True)
        position = _ledger_number(raw["position"], field="position", line=line, required=True)
        if not block.is_integer() or block < 1:
            raise ValueError(f"대장 {line}행: block은 1 이상의 정수여야 합니다.")
        if position not in (1, 2, 3, 4):
            raise ValueError(f"대장 {line}행: position은 1~4여야 합니다.")
        run_id = (raw["run_id"] or "").strip()
        app = (raw["app"] or "").strip().lower()
        if not run_id:
            raise ValueError(f"대장 {line}행: run_id가 비었습니다.")
        if app not in APPS:
            raise ValueError(f"대장 {line}행: app은 toss 또는 kis여야 합니다.")
        added = _ledger_number(raw["added_rtt_ms"], field="added_rtt_ms", line=line, required=True)
        if added > MAX_ADDED_RTT_MS:
            raise ValueError(f"대장 {line}행: added_rtt_ms는 {MAX_ADDED_RTT_MS} 이하여야 합니다.")
        outcome = (raw["outcome"] or "").strip()
        if outcome not in LEDGER_OUTCOMES:
            raise ValueError(f"대장 {line}행: outcome이 비었거나 허용값이 아닙니다.")
        ping = _ledger_number(raw.get("ping_rtt_ms"), field="ping_rtt_ms", line=line, required=False)
        if ping == 0:
            raise ValueError(f"대장 {line}행: ping_rtt_ms는 0보다 커야 합니다.")
        key = (run_id, app)
        if key in seen:
            raise ValueError(f"대장 {line}행: run_id {run_id}의 {app} 행이 중복됩니다.")
        seen.add(key)
        block = int(block)
        if block_levels.setdefault(block, added) != added:
            raise ValueError(f"대장 {line}행: 블록 {block} 안에서 added_rtt_ms가 다릅니다.")
        if run_blocks.setdefault(run_id, block) != block:
            raise ValueError(f"대장 {line}행: run_id {run_id}가 두 블록에 걸쳐 있습니다.")
        rows.append({
            "block": block, "position": int(position), "run_id": run_id, "app": app,
            "added_rtt_ms": added, "outcome": outcome, "ping_rtt_ms": ping,
            "network_type": (raw.get("network_type") or "").strip() or None,
        })
    if not rows:
        raise ValueError("RTT 대장에 시도 행이 없습니다.")
    if len(set(block_levels.values())) < 2:
        raise ValueError("RTT 대장에 지연 단계가 두 개 이상 있어야 합니다.")
    return rows


def _ols(points: list[tuple[float, float]]) -> tuple[float, float, float | None] | None:
    """Least-squares (slope, intercept, residual SD) of y on x, or None when undefined."""
    if len(points) < 3:
        return None
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    if sxx == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sxx
    intercept = mean_y - slope * mean_x
    residual = sum((y - intercept - slope * x) ** 2 for x, y in points)
    return slope, intercept, math.sqrt(residual / (len(points) - 2))


def _seeded_rng(points: list[tuple[int, float, float]]) -> random.Random:
    # Input-derived seed keeps the interval identical across runs, as in analysis.py.
    data = ";".join(f"{block},{x.hex()},{y.hex()}" for block, x, y in sorted(points)).encode("ascii")
    return random.Random(int.from_bytes(hashlib.sha256(data).digest()[:8], "big"))


def _fit(points: list[tuple[int, float, float]], base_rtt: float | None = None) -> dict[str, Any] | None:
    """OLS of (block, x, y) points with a block bootstrap, which keeps each block's attempts together."""
    estimate = _ols([(x, y) for _, x, y in points])
    if estimate is None:
        return None
    slope, intercept, residual_sd = estimate
    blocks: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for block, x, y in points:
        blocks[block].append((x, y))
    result: dict[str, Any] = {
        "slope_k": round(slope, 4),
        "intercept_ms": round(intercept, 3),
        "residual_sd_ms": round(residual_sd, 3) if residual_sd is not None else None,
        "observations": len(points),
        "blocks": len(blocks),
        "bootstrap_95_ci": None,
        "bootstrap_replicates_used": 0,
    }
    if base_rtt is not None:
        result["base_rtt_ms"] = base_rtt
        result["network_share_ms"] = round(slope * base_rtt, 3)
        result["non_network_ms"] = round(intercept - slope * base_rtt, 3)
    if len(blocks) < _MIN_BOOTSTRAP_BLOCKS:
        return result
    rng = _seeded_rng(points)
    keys = sorted(blocks)
    slopes, intercepts = [], []
    for _ in range(_BOOTSTRAP_REPLICATES):
        sample = [point for _ in keys for point in blocks[keys[rng.randrange(len(keys))]]]
        replicate = _ols(sample)
        if replicate is not None:
            slopes.append(replicate[0])
            intercepts.append(replicate[1])
    if not slopes:
        return result
    ci: dict[str, list[float]] = {
        "slope_k": [round(_percentile(slopes, 0.025), 4), round(_percentile(slopes, 0.975), 4)],
        "intercept_ms": [round(_percentile(intercepts, 0.025), 3), round(_percentile(intercepts, 0.975), 3)],
    }
    if base_rtt is not None:
        shares = [k * base_rtt for k in slopes]
        rest = [a - k * base_rtt for k, a in zip(slopes, intercepts)]
        ci["network_share_ms"] = [round(_percentile(shares, 0.025), 3), round(_percentile(shares, 0.975), 3)]
        ci["non_network_ms"] = [round(_percentile(rest, 0.025), 3), round(_percentile(rest, 0.975), 3)]
    result["bootstrap_95_ci"] = ci
    result["bootstrap_replicates_used"] = len(slopes)
    return result


def _level_rows(points: list[tuple[int, float, float]], levels: list[float],
                fit: dict[str, Any] | None) -> list[dict[str, Any]]:
    rows = []
    for level in levels:
        values = [y for _, x, y in points if x == level]
        rows.append({
            "added_rtt_ms": level,
            "count": len(values),
            "median_ms": round(median(values), 3) if values else None,
            "fitted_ms": round(fit["intercept_ms"] + fit["slope_k"] * level, 3) if fit else None,
        })
    return rows


def fit_session(observations: Iterable[Mapping[str, object]], ledger: list[dict[str, Any]], *,
                base_rtt: Mapping[str, float] | None = None) -> dict[str, Any]:
    """Join observations to the ledger and fit visible time against added RTT per app.

    Only attempts that the ledger *and* the observation both mark completed are
    fitted.  Observations must share one comparison condition apart from
    ``network_type``, which is expected to name the delay level.
    """
    base_rtt = dict(base_rtt or {})
    for app, value in base_rtt.items():
        if app not in APPS or not math.isfinite(value) or value <= 0:
            raise ValueError("base_rtt는 toss/kis별 0보다 큰 ms 값이어야 합니다.")
    normalized = [_normalize(row, index) for index, row in enumerate(observations, start=1)]
    by_key = {(row["run_id"], row["app"]): row for row in ledger}
    levels = sorted({row["added_rtt_ms"] for row in ledger})
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in normalized:
        grouped[(row["run_id"], row["app"])].append(row)

    exclusions: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    status_mismatch, type_mismatch = [], []
    joined: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for key in sorted(grouped):
        rows = grouped[key]
        attempt = by_key.get(key)
        reason = None
        if attempt is None:
            reason = "not_in_ledger"
        elif len(rows) > 1:
            reason = "duplicate_observation"
        else:
            row = rows[0]
            if attempt["outcome"] != row["status"]:
                status_mismatch.append(f"{key[0]}/{key[1]}")
                reason = "ledger_status_mismatch"
            elif row["status"] != "completed":
                reason = "noncompleted"
            if attempt["network_type"] and attempt["network_type"] != row["network_type"]:
                type_mismatch.append(f"{key[0]}/{key[1]}")
        if reason:
            exclusions.append({"run_id": key[0], "app": key[1], "reason": reason})
        else:
            joined.append((rows[0], attempt))
    if status_mismatch:
        warnings.append({"code": "ledger_status_mismatch", "detail": ", ".join(status_mismatch)})
    if type_mismatch:
        warnings.append({"code": "network_type_mismatch", "detail": ", ".join(type_mismatch)})
    if not joined:
        raise ValueError("대장과 맞는 완료 관측값이 없습니다.")

    varying = [field for field in _CONDITION_FIELDS if len({row[field] for row, _ in joined}) > 1]
    if varying:
        raise ValueError(f"RTT 분석은 한 조건만 다룹니다. 값이 섞인 항목: {', '.join(varying)}")
    conditions = {field: joined[0][0][field] for field in _CONDITION_FIELDS}

    attempts = {
        app: {level: {outcome: 0 for outcome in LEDGER_OUTCOMES} for level in levels} for app in APPS
    }
    for attempt in ledger:
        attempts[attempt["app"]][attempt["added_rtt_ms"]][attempt["outcome"]] += 1
    noncompleted = sum(
        counts[outcome] for app in APPS for counts in attempts[app].values()
        for outcome in ("timeout", "error")
    )
    if noncompleted:
        warnings.append({"code": "noncompleted_excluded", "detail": str(noncompleted)})

    points = {
        app: [(attempt["block"], attempt["added_rtt_ms"], row["elapsed_ms"])
              for row, attempt in joined if row["app"] == app]
        for app in APPS
    }
    apps: dict[str, Any] = {}
    for app in APPS:
        fit = _fit(points[app], base_rtt.get(app))
        apps[app] = {"fit": fit, "levels": _level_rows(points[app], levels, fit)}

    by_run: dict[str, dict[str, tuple[int, float, float]]] = defaultdict(dict)
    for row, attempt in joined:
        by_run[row["run_id"]][row["app"]] = (attempt["block"], attempt["added_rtt_ms"], row["elapsed_ms"])
    differences = [
        (pair["toss"][0], pair["toss"][1], pair["toss"][2] - pair["kis"][2])
        for _, pair in sorted(by_run.items()) if set(pair) == set(APPS)
    ]
    difference_fit = _fit(differences)
    difference_levels = _level_rows(differences, levels, difference_fit)
    preliminary = any(level["count"] < _MIN_PAIRS_PER_LEVEL for level in difference_levels)

    return {
        "schema_version": 1,
        "conditions": conditions,
        "levels_ms": levels,
        "x_basis": "planned_added_rtt_ms",
        "attempts": [
            {"app": app, "added_rtt_ms": level, **attempts[app][level]} for level in levels for app in APPS
        ],
        "apps": apps,
        "difference": {"pairs": len(differences), "fit": difference_fit, "levels": difference_levels},
        "verification": _verification(ledger, levels, warnings),
        "exclusions": exclusions,
        "warnings": warnings,
        "preliminary": preliminary,
        "interpretation": {
            "slope_k": (
                "Milliseconds of visible completion time per millisecond of added RTT; "
                "effective count of serial round trips on the screen's critical path"
            ),
            "intercept_ms": "Fitted visible time at the session's baseline network (added RTT 0)",
            "difference": "Toss minus KIS per pair; its slope is the gap's growth per ms of RTT",
            "bootstrap_ci": (
                "Percentile bootstrap that resamples whole blocks; "
                f"shown only with at least {_MIN_BOOTSTRAP_BLOCKS} blocks"
            ),
            "preliminary": f"True when any delay level has fewer than {_MIN_PAIRS_PER_LEVEL} completed pairs",
            "causality": (
                "The slope shows sensitivity to added RTT under this setup; it does not identify "
                "the protocol, server design, or which requests waited"
            ),
        },
    }


def _verification(ledger: list[dict[str, Any]], levels: list[float],
                  warnings: list[dict[str, str]]) -> dict[str, Any]:
    """Compare phone ping per block against the plan; baseline is the 0 ms level."""
    pings: dict[int, list[float]] = defaultdict(list)
    block_level: dict[int, float] = {}
    for row in ledger:
        block_level[row["block"]] = row["added_rtt_ms"]
        if row["ping_rtt_ms"] is not None:
            pings[row["block"]].append(row["ping_rtt_ms"])
    block_ping = {block: median(values) for block, values in pings.items()}
    zero = [block_ping[block] for block, level in block_level.items() if level == 0 and block in block_ping]
    baseline = median(zero) if zero else None
    if 0 not in levels:
        warnings.append({"code": "no_zero_level", "detail": ""})
    rows, missing, off = [], [], []
    for level in levels:
        blocks = [block for block, value in block_level.items() if value == level]
        measured = [block_ping[block] for block in blocks if block in block_ping]
        if len(measured) < len(blocks):
            missing.append(f"{level:g}")
        ping = median(measured) if measured else None
        added = ping - baseline if ping is not None and baseline is not None else None
        if added is not None and abs(added - level) > max(VERIFY_TOLERANCE_MS, VERIFY_TOLERANCE_RATIO * level):
            off.append(f"{level:g}")
        rows.append({
            "added_rtt_ms": level,
            "blocks": len(blocks),
            "blocks_with_ping": len(measured),
            "median_ping_ms": round(ping, 3) if ping is not None else None,
            "measured_added_ms": round(added, 3) if added is not None else None,
        })
    if missing:
        warnings.append({"code": "missing_ping", "detail": ", ".join(missing)})
    if off:
        warnings.append({"code": "rtt_verification_mismatch", "detail": ", ".join(off)})
    return {"baseline_ping_ms": round(baseline, 3) if baseline is not None else None, "levels": rows}


# --------------------------------------------------------------------------
# Report

_APP_LABELS = {"toss": "토스", "kis": "한국투자"}
_WARNING_TEXT = {
    "ledger_status_mismatch": "대장 outcome과 관측 status가 달라 제외한 시도",
    "network_type_mismatch": "대장의 network_type과 입력값이 다른 시도",
    "noncompleted_excluded": "시간 초과·앱 오류로 적합에서 빠진 시도 수. 높은 지연에 몰려 있으면 k가 낮게 나온다",
    "missing_ping": "ping 기록이 없는 블록이 있는 지연 단계(ms)",
    "rtt_verification_mismatch": "ping으로 잰 추가 RTT가 계획과 크게 다른 지연 단계(ms)",
    "no_zero_level": "0ms 단계가 없어 ping 기준선을 정할 수 없음",
}
_EXCLUSION_TEXT = {
    "not_in_ledger": "대장에 없음",
    "duplicate_observation": "같은 시도의 관측값 중복",
    "ledger_status_mismatch": "대장 outcome과 status 불일치",
    "noncompleted": "완료되지 않음",
}


def _fmt(value: Any, digits: int = 1) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _interval(fit: dict[str, Any] | None, key: str, digits: int = 1) -> str:
    if not fit or not fit["bootstrap_95_ci"] or key not in fit["bootstrap_95_ci"]:
        return "-"
    low, high = fit["bootstrap_95_ci"][key]
    return f"{low:.{digits}f} ~ {high:.{digits}f}"


def render_markdown(result: dict[str, Any]) -> str:
    fits = {app: result["apps"][app]["fit"] for app in APPS}
    difference = result["difference"]["fit"]
    lines = [
        "# RTT 단계별 화면 시간 분석",
        "",
        "> 해석 범위: 기울기 k는 추가한 왕복 지연 1ms당 화면 완료 시간이 몇 ms 늘었는지다. "
        "화면이 기다린 직렬 네트워크 왕복의 실효 횟수로 읽는다. 병렬 요청은 1회로, "
        "큰 응답의 추가 왕복·재전송·재시도는 더해서 나타난다. 이 값은 이 기기·이 망 구성에서 관찰한 "
        "지연 민감도이며, 프로토콜·서버 구조·어떤 요청이 사슬을 이뤘는지는 확정하지 않는다.",
        "",
    ]
    if result["preliminary"]:
        lines += [
            f"> **예비 결과:** 완료 페어가 {_MIN_PAIRS_PER_LEVEL}쌍 미만인 지연 단계가 있다.",
            "",
        ]
    lines += ["## 조건", "", "| 항목 | 값 |", "|---|---|"]
    for field, value in result["conditions"].items():
        lines.append(f"| `{field}` | {_fmt(value)} |")
    lines += [
        f"| 지연 단계(ms) | {', '.join(f'{level:g}' for level in result['levels_ms'])} |",
        "| x축 | 계획한 추가 RTT(대장 `added_rtt_ms`) |",
        "",
        "## 시도 결과 (대장 기준)",
        "",
        "| 추가 RTT(ms) | 앱 | 완료 | 시간 초과 | 앱 오류 | 촬영 실패 | 미시작 |",
        "|---:|---|---:|---:|---:|---:|---:|",
    ]
    for row in result["attempts"]:
        lines.append(
            f"| {row['added_rtt_ms']:g} | {_APP_LABELS[row['app']]} | {row['completed']} | {row['timeout']} | "
            f"{row['error']} | {row['invalid_capture']} | {row['not_started']} |"
        )
    lines += [
        "",
        "## 단계별 중앙값과 직선 적합값",
        "",
        "중앙값이 적합값에서 한 방향으로 계속 벗어나면 직선 모형이 맞지 않는다. "
        "예를 들어 어느 지연부터 갑자기 늘면 시간 초과·재시도·경로 전환을 먼저 의심한다.",
        "",
        "| 추가 RTT(ms) | 토스 n | 토스 중앙값(ms) | 토스 적합값(ms) | 한투 n | 한투 중앙값(ms) | 한투 적합값(ms) "
        "| 페어 | 토스−한투 중앙값(ms) |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for position, level in enumerate(result["levels_ms"]):
        toss = result["apps"]["toss"]["levels"][position]
        kis = result["apps"]["kis"]["levels"][position]
        pair = result["difference"]["levels"][position]
        lines.append(
            f"| {level:g} | {toss['count']} | {_fmt(toss['median_ms'])} | {_fmt(toss['fitted_ms'])} | "
            f"{kis['count']} | {_fmt(kis['median_ms'])} | {_fmt(kis['fitted_ms'])} | "
            f"{pair['count']} | {_fmt(pair['median_ms'])} |"
        )
    lines += [
        "",
        "## 기울기 k",
        "",
        "| 항목 | 토스 | 한국투자 | 토스−한국투자(페어) |",
        "|---|---:|---:|---:|",
    ]
    rows = [
        ("k (ms/ms)", lambda fit: _fmt(fit["slope_k"], 2) if fit else "-"),
        ("k 95% 블록 부트스트랩", lambda fit: _interval(fit, "slope_k", 2)),
        ("추가 0ms 적합값(ms)", lambda fit: _fmt(fit["intercept_ms"]) if fit else "-"),
        ("적합값 95% 블록 부트스트랩", lambda fit: _interval(fit, "intercept_ms")),
        ("잔차 표준편차(ms)", lambda fit: _fmt(fit["residual_sd_ms"]) if fit else "-"),
        ("사용한 관측 / 블록", lambda fit: f"{fit['observations']} / {fit['blocks']}" if fit else "-"),
    ]
    for title, getter in rows:
        lines.append(f"| {title} | {getter(fits['toss'])} | {getter(fits['kis'])} | {getter(difference)} |")
    if any(fit and "base_rtt_ms" in fit for fit in fits.values()):
        lines += [
            "",
            "## 기본 망에서의 대략적 분해",
            "",
            "`망 몫 = k × 기본 RTT`, `망 외 시간 = 추가 0ms 적합값 − 망 몫`. 기본 RTT는 별도 캡처에서 잰 값이라 "
            "같은 시도의 측정이 아니며, 망 외 시간에는 클라이언트 처리·서버 처리·렌더링·측정 오차가 섞여 있다.",
            "",
            "| 항목 | 토스 | 한국투자 |",
            "|---|---:|---:|",
        ]
        decomposition = [
            ("기본 RTT 입력(ms)", lambda fit: _fmt(fit.get("base_rtt_ms"))),
            ("망 몫(ms)", lambda fit: _fmt(fit.get("network_share_ms"))),
            ("망 몫 95% 구간", lambda fit: _interval(fit, "network_share_ms")),
            ("망 외 시간(ms)", lambda fit: _fmt(fit.get("non_network_ms"))),
            ("망 외 시간 95% 구간", lambda fit: _interval(fit, "non_network_ms")),
        ]
        for title, getter in decomposition:
            lines.append(
                f"| {title} | {getter(fits['toss']) if fits['toss'] else '-'} | "
                f"{getter(fits['kis']) if fits['kis'] else '-'} |"
            )
    verification = result["verification"]
    lines += [
        "",
        "## 지연 주입 검증 (휴대폰 ping)",
        "",
        f"0ms 단계 ping 중앙값: {_fmt(verification['baseline_ping_ms'])}ms. "
        "`측정 추가 RTT`는 단계 ping 중앙값에서 이 기준선을 뺀 값이다.",
        "",
        "| 계획 추가 RTT(ms) | 블록 | ping 기록 블록 | ping 중앙값(ms) | 측정 추가 RTT(ms) |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in verification["levels"]:
        lines.append(
            f"| {row['added_rtt_ms']:g} | {row['blocks']} | {row['blocks_with_ping']} | "
            f"{_fmt(row['median_ping_ms'])} | {_fmt(row['measured_added_ms'])} |"
        )
    lines += ["", "## 경고와 제외", ""]
    for warning in result["warnings"]:
        detail = f": {warning['detail']}" if warning["detail"] else ""
        lines.append(f"- 경고 `{warning['code']}` {_WARNING_TEXT.get(warning['code'], '')}{detail}")
    reasons: dict[str, int] = defaultdict(int)
    for exclusion in result["exclusions"]:
        reasons[exclusion["reason"]] += 1
    for reason, count in sorted(reasons.items()):
        lines.append(f"- 제외 {_EXCLUSION_TEXT.get(reason, reason)}: {count}건")
    if not result["warnings"] and not reasons:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"
