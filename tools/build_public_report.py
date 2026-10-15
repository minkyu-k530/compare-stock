"""Build a standalone, aggregate-only portfolio report from real measurements.

The report deliberately uses an allowlist of analyzed numbers and fixed labels.
Free-text fields, individual observations, run IDs, notes, video references, and
protocol observations cannot flow into the generated HTML.
"""

from __future__ import annotations

import argparse
import csv
from html import escape
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from stocklab.analysis import analyze  # noqa: E402


METHOD_LABELS = {
    "screen_recording": "휴대폰 화면 녹화",
    "external_camera": "외부 카메라 촬영",
    "manual_other": "기타 수동 측정",
    "unspecified": "촬영 방식 미기록",
}
TIMING_LABELS = {
    "frame_timestamps": "프레임 표시 시각(PTS)",
    "fixed_fps_frames": "고정 FPS와 프레임 번호",
    "playback_seek": "재생 위치 근사값",
    "manual": "수동 입력",
    "unspecified": "시간 근거 미기록",
}
STATE_LABELS = {"warm": "웜", "cold": "콜드", "unspecified": "시작 상태 미기록"}
MARKET_LABELS = {"open": "장중", "closed": "장외", "unspecified": "시장 상태 미기록"}
KEYBOARD_LABELS = {
    "shown": "키보드 표시됨", "hidden": "키보드 숨김",
    "not_applicable": "키보드 해당 없음", "unspecified": "키보드 상태 미기록",
}
LEDGER_OUTCOMES = ("completed", "timeout", "error", "invalid_capture", "not_started")


def read_analysis(db_path: Path) -> dict:
    """Open the existing database read-only and exclude demo observations."""
    path = db_path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"측정 데이터베이스를 찾을 수 없습니다: {path}")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        rows = (
            dict(row)
            for row in connection.execute(
                "SELECT * FROM observations WHERE source IN ('manual', 'imported') ORDER BY id"
            )
        )
        return analyze(rows)
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"측정 데이터베이스를 읽지 못했습니다: {exc}") from exc
    finally:
        connection.close()


def read_ledger_outcomes(ledger_path: Path) -> dict[str, int]:
    """Count only outcome values from the preplanned 60-attempt main ledger.

    CSV's other columns are parsed to locate ``outcome`` but are never copied to
    the return value or report. A blank or unknown outcome blocks publication.
    """
    path = ledger_path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"시도 대장을 찾을 수 없습니다: {path}")
    counts = {outcome: 0 for outcome in LEDGER_OUTCOMES}
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "outcome" not in reader.fieldnames:
            raise ValueError("시도 대장에 outcome 열이 필요합니다.")
        for row_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"시도 대장 {row_number}행의 CSV 열 개수가 맞지 않습니다.")
            outcome = row.get("outcome")
            if outcome not in counts:
                raise ValueError(f"시도 대장 {row_number}행의 outcome이 비었거나 허용값이 아닙니다.")
            counts[outcome] += 1
            if sum(counts.values()) > 60:
                raise ValueError("첫 본 실험 시도 대장은 정확히 60행이어야 합니다.")
    if sum(counts.values()) != 60:
        raise ValueError("첫 본 실험 시도 대장은 정확히 60행이어야 합니다.")
    return counts


def _number(value: float | int | None, digits: int = 1) -> str:
    return "—" if value is None else f"{value:,.{digits}f}"


def _safe_label(labels: dict[str, str], value: str) -> str:
    # Never fall back to a database value; it may contain private information.
    return labels.get(value, "기타")


def _stock_code_label(code: str) -> str:
    """Show only a validated domestic ticker identifier, never arbitrary row text."""
    if code == "unspecified":
        return "종목 미기록"
    if code == "not_applicable":
        return "종목 해당 없음"
    if re.fullmatch(r"[0-9]{6}", code):
        return f"종목 코드 {code}"
    return "종목 코드 형식 오류"


def _group_html(number: int, group: dict) -> str:
    pair_count = group["paired_runs"]
    metrics = group["metrics"].get("elapsed_ms")
    statuses = group["status_counts"]
    excluded = group["excluded_groups"]
    excluded_counts: dict[str, int] = {}
    for item in excluded:
        reason = item["reason"]
        excluded_counts[reason] = excluded_counts.get(reason, 0) + 1
    label = "종목 상세 진입" if group["scenario"] == "search_result_to_detail_quote" else "기타 시나리오"
    condition = " · ".join((
        label,
        _stock_code_label(group["stock_code"]),
        _safe_label(KEYBOARD_LABELS, group["keyboard_state"]),
        _safe_label(METHOD_LABELS, group["capture_method"]),
        _safe_label(TIMING_LABELS, group["timing_basis"]),
        _safe_label(STATE_LABELS, group["start_state"]),
        _safe_label(MARKET_LABELS, group["market_phase"]),
    ))
    status_rows = "".join(
        f"<tr><th scope='row'>{escape(app_label)}</th>"
        f"<td>{statuses[app]['completed']}</td><td>{statuses[app]['timeout']}</td>"
        f"<td>{statuses[app]['error']}</td></tr>"
        for app, app_label in (("toss", "토스증권"), ("kis", "한국투자 앱"))
    )
    if metrics is None:
        metrics_html = "<p class='empty'>비교 가능한 완료 쌍이 없어 지연시간 결과를 표시하지 않습니다.</p>"
    else:
        diff = metrics["toss_minus_kis"]
        ci = diff["bootstrap_95_ci"]
        ci_text = (
            "표본 5쌍 미만: 신뢰구간 미표시"
            if ci is None else
            f"탐색적 95% 부트스트랩 구간: {_number(ci[0])} ~ {_number(ci[1])} ms"
        )
        p95_text = (
            f"P95 토스 {_number(metrics['toss']['p95'])} ms · "
            f"한국투자 {_number(metrics['kis']['p95'])} ms"
            if metrics["toss"]["p95"] is not None else
            "완료 쌍 20개 미만: P95 미표시"
        )
        metrics_html = (
            "<div class='cards'>"
            f"<div><span>토스 중앙값</span><strong>{_number(metrics['toss']['median'])} ms</strong></div>"
            f"<div><span>한국투자 중앙값</span><strong>{_number(metrics['kis']['median'])} ms</strong></div>"
            f"<div><span>쌍별 차이의 중앙값</span><strong>{_number(diff['median'])} ms</strong></div>"
            "</div>"
            f"<p class='fine'>토스 − 한국투자. 음수는 이 표본에서 토스의 관찰 시간이 짧았다는 뜻입니다. "
            f"{escape(ci_text)}. {escape(p95_text)}.</p>"
            f"<p class='fine'>쌍별 순위: 토스 짧음 {diff['toss_lower_runs']}회 · "
            f"동일 {diff['equal_runs']}회 · 한국투자 짧음 {diff['kis_lower_runs']}회.</p>"
        )
    exclusion_text = " · ".join(
        f"{escape({'missing_counterpart': '상대 앱 기록 없음', 'duplicate_or_ambiguous': '중복 또는 불명확', 'condition_mismatch': '조건 불일치', 'noncompleted': '미완료'}.get(reason, '기타'))} {count}그룹"
        for reason, count in sorted(excluded_counts.items())
    ) or "없음"
    quality = group["quality_checks"]
    toss_versions = quality["app_versions"]["toss"]
    kis_versions = quality["app_versions"]["kis"]
    order = quality["run_order"]
    preliminary = " <span class='badge'>예비 결과 · 유효 20쌍 미만</span>" if pair_count < 20 else ""
    quality_html = (
        "<p class='fine'>완료된 쌍의 기록 품질: "
        f"앱 버전 미기록 토스 {toss_versions['missing']}쌍 · 한국투자 {kis_versions['missing']}쌍; "
        f"버전 혼합 토스 {'있음' if toss_versions['mixed'] else '없음'} · "
        f"한국투자 {'있음' if kis_versions['mixed'] else '없음'}; "
        f"실행 순서 토스 먼저 {order['toss_first']}쌍 · "
        f"한국투자 먼저 {order['kis_first']}쌍 · "
        f"순서 미기록 {order['unrecorded']}쌍 · 순서 중복 {order['duplicate']}쌍.</p>"
    )
    completeness = quality["condition_completeness"]
    if not completeness["stock_code_recorded"] or not completeness["keyboard_state_recorded"]:
        missing = []
        if not completeness["stock_code_recorded"]:
            missing.append("종목 코드")
        if not completeness["keyboard_state_recorded"]:
            missing.append("키보드 상태")
        quality_html += f"<p class='fine'>조건 미기록: {escape(', '.join(missing))}. 명시 조건 그룹과 합치지 않았습니다.</p>"
    return (
        f"<section><h2>조건 그룹 {number}{preliminary}</h2><p class='condition'>{escape(condition)}</p>"
        f"<p>완료된 비교 쌍 <strong>{pair_count}</strong>개 · 입력 기록 {group['input_rows']}건</p>"
        f"{metrics_html}"
        "<table><caption>시도 결과 — 완료에는 쌍에서 제외된 단독 기록도 포함</caption>"
        "<thead><tr><th>앱</th><th>완료</th><th>시간 초과</th><th>오류</th></tr></thead>"
        f"<tbody>{status_rows}</tbody></table>"
        f"<p class='fine'>비교에서 제외된 그룹: {exclusion_text}.</p>"
        f"{quality_html}"
        "</section>"
    )


def render_report(result: dict, ledger_counts: dict[str, int] | None = None) -> str:
    """Render only an explicit allowlist of aggregate fields from analysis."""
    groups = result["scenarios"].values()
    group_html = "".join(_group_html(i, group) for i, group in enumerate(groups, start=1))
    if result["input_rows"] == 0:
        main = "<section class='empty'><h2>실측 결과 없음</h2><p>실제 앱 측정값이 아직 등록되지 않았습니다. 속도 우위나 원인을 주장할 수 없습니다.</p></section>"
    else:
        main = (
            f"<p class='summary'>실제 측정 기록 {result['input_rows']}건 · "
            f"조건 그룹 {result['analysis_group_count']}개 · "
            f"완료 비교 쌍 {result['total_paired_runs']}개 · "
            f"시간 초과/오류 기록 {result['noncompleted_observations']}건</p>"
            + group_html
        )
    if ledger_counts is None:
        ledger_html = (
            "<section class='empty'><h2>계획된 시도 대장 미포함</h2>"
            "<p>대장이 없어 촬영 실패(invalid_capture)와 미시작(not_started) 건수가 이 보고서에 포함되지 않았습니다. "
            "앱 기록 건수는 계획된 60회 시도의 완료율이 아닙니다.</p></section>"
        )
    else:
        started = 60 - ledger_counts["not_started"]
        ledger_html = (
            "<section><h2>본 실험 60회 계획 대장</h2>"
            f"<p>계획 60회 · 시작 {started}회 · 완료 {ledger_counts['completed']}회 · "
            f"시간 초과 {ledger_counts['timeout']}회 · 앱 오류 {ledger_counts['error']}회 · "
            f"촬영 실패 {ledger_counts['invalid_capture']}회 · 미시작 {ledger_counts['not_started']}회</p>"
            "<p class='fine'>대장의 outcome 열만 집계했습니다. 개별 시도와 앱 측정 DB의 일치 여부는 이 보고서가 검증하지 않습니다.</p>"
            "</section>"
        )
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<title>모바일 증권 앱 반응시간 비교 — 공개 요약</title>
<style>
body{{font-family:system-ui,-apple-system,sans-serif;max-width:880px;margin:auto;padding:32px 20px;color:#17222b;background:#f6f8fa;line-height:1.6}}
h1{{font-size:2rem;line-height:1.2}}h2{{font-size:1.25rem;margin-top:0}}section{{background:white;border:1px solid #dce3e8;border-radius:12px;padding:24px;margin:20px 0}}
.summary,.condition,.fine{{color:#4d5d69}}.fine{{font-size:.9rem}}.empty{{background:#fff8e5}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:18px 0}}
.cards div{{border:1px solid #dce3e8;border-radius:8px;padding:14px}}.cards span{{display:block;font-size:.9rem;color:#52616c}}.cards strong{{font-size:1.4rem}}
.badge{{display:inline-block;font-size:.75rem;font-weight:600;background:#fff0bf;color:#664400;border-radius:999px;padding:2px 8px;vertical-align:middle}}
table{{border-collapse:collapse;width:100%;margin-top:18px}}caption{{text-align:left;font-size:.9rem;color:#52616c;margin-bottom:6px}}th,td{{border-bottom:1px solid #e1e6eb;padding:8px;text-align:left}}
</style></head><body>
<header><h1>모바일 증권 앱 반응시간 비교</h1><p>토스증권 앱과 한국투자 앱의 화면에 보이는 완료 시간을 같은 조건에서 쌍으로 비교합니다.</p></header>
<main>{ledger_html}{main}</main>
<section><h2>측정과 해석의 범위</h2>
<p>동일 시나리오, 촬영 방식, 시간 판독 근거, 앱 시작 상태, 시장 상태, 완료 기준, 촬영 FPS, 기기, 네트워크 조건별로 따로 집계합니다. 중앙값은 완료된 쌍에서만 계산합니다. 실패와 짝이 없는 기록은 지연시간 계산에서 제외하고 시도 건수에 남깁니다.</p>
<p>화면 녹화는 녹화 부하와 터치 표시 지연의 영향을 받을 수 있습니다. PTS를 사용해도 화면에서 보인 시간만 측정하며 서버 처리 시간이나 앱 내부 전송 방식을 직접 측정하지 않습니다. 표본이 5쌍 미만이면 탐색적 신뢰구간, 20쌍 미만이면 P95를 표시하지 않습니다.</p>
<p>두 앱의 관찰 시간 차이가 있더라도 특정 프로토콜, 서버 구현, 캐시 또는 렌더링 단계가 원인이라고 단정할 수 없습니다. 원본 영상, 프레임 선택 근거, 실험 순서와 앱 버전은 별도로 검토해야 합니다.</p>
</section>
</body></html>"""


def write_report(output: Path, content: str) -> None:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=".public-report-", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "stocklab.sqlite3")
    parser.add_argument("--ledger", type=Path, help="Completed 60-row main trial ledger CSV")
    parser.add_argument("--out", type=Path, required=True, help="Standalone public HTML output path")
    args = parser.parse_args()
    if args.db.expanduser().resolve() == args.out.expanduser().resolve():
        parser.error("출력 경로는 데이터베이스 경로와 달라야 합니다.")
    if args.ledger is not None and args.ledger.expanduser().resolve() == args.out.expanduser().resolve():
        parser.error("출력 경로는 시도 대장 경로와 달라야 합니다.")
    try:
        result = read_analysis(args.db)
        ledger_counts = read_ledger_outcomes(args.ledger) if args.ledger is not None else None
        write_report(args.out, render_report(result, ledger_counts))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(f"공개용 집계 보고서: {args.out.expanduser().resolve()}")


if __name__ == "__main__":
    main()
