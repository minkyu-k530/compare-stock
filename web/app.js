const state = { source: 'real', rows: [], analysis: null, scenario: null, videoUrl: null, tapAt: null, contentAt: null };
const $ = (id) => document.getElementById(id);
const labels = {
  search_result_to_detail_quote: '검색 결과 → 종목 상세',
  stock_search: '종목 검색', quote_detail: '종목 상세', chart_open: '차트 열기',
  app_launch: '증권 화면 시작', cold_launch: '콜드 시작', warm_navigation: '웜 탐색'
};
const scenarioLabel = (name) => labels[name] || name;
const methodLabels = {
  external_camera: '외부 촬영', screen_recording: '화면 녹화',
  manual_other: '기타', unspecified: '촬영 방식 미기록'
};
const methodLabel = (name) => methodLabels[name] || name;
const timingLabels = {
  fixed_fps_frames: '고정 FPS 프레임', frame_timestamps: '프레임 PTS',
  playback_seek: '재생 위치 근사', manual: '수동 판독', unspecified: '판독 근거 미기록'
};
const startLabels = { warm: '웜', cold: '콜드', unspecified: '시작 상태 미기록' };
const marketLabels = { open: '장중', closed: '장외', unspecified: '시장 상태 미기록' };
const keyboardLabels = { hidden: '키보드 숨김', shown: '키보드 표시됨', not_applicable: '키보드 해당 없음', unspecified: '키보드 상태 미기록' };
const stockLabel = (code) => code === 'not_applicable' ? '종목 해당 없음' : code === 'unspecified' ? '종목 미기록' : `종목 ${code}`;
const statusLabels = { completed: '완료', timeout: '시간 초과', error: '오류' };
const exclusionLabels = {
  missing_counterpart: '다른 앱 기록 없음', duplicate_or_ambiguous: '중복 또는 앱 구분 모호',
  condition_mismatch: '기기 또는 네트워크 불일치', noncompleted: '시간 초과 또는 오류'
};
const groupLabel = (result) => [
  scenarioLabel(result.scenario), stockLabel(result.stock_code),
  keyboardLabels[result.keyboard_state] || '키보드 상태 미기록',
  methodLabel(result.capture_method),
  timingLabels[result.timing_basis] || result.timing_basis,
  startLabels[result.start_state] || result.start_state,
  marketLabels[result.market_phase] || result.market_phase,
  result.device || '기기 미기록', result.network_type || '네트워크 미기록',
  result.capture_fps == null ? '' : `${number(result.capture_fps, 1)}fps`,
  result.completion_criterion === 'unspecified' ? '' : result.completion_criterion
].filter(Boolean).join(' · ');
const groupMatchesRow = (result, row) =>
  result.scenario === row.scenario &&
  result.stock_code === (row.stock_code || 'unspecified') &&
  result.keyboard_state === (row.keyboard_state || 'unspecified') &&
  result.capture_method === row.capture_method &&
  result.timing_basis === row.timing_basis &&
  result.start_state === row.start_state &&
  result.market_phase === row.market_phase &&
  result.device === row.device &&
  result.network_type === row.network_type &&
  result.completion_criterion === row.completion_criterion &&
  Number(result.capture_fps || 0) === Number(row.capture_fps || 0);
const number = (value, digits = 0) => Number(value).toLocaleString('ko-KR', { maximumFractionDigits: digits });
const ms = (value) => value == null ? '—' : `${number(value, 1)} ms`;
const toast = (message, error = false) => {
  const box = $('toast'); box.textContent = message; box.classList.toggle('error', error);
  box.classList.add('show'); clearTimeout(toast.timer);
  toast.timer = setTimeout(() => box.classList.remove('show'), 3800);
};

async function api(path, options = {}) {
  const response = await fetch(path, options);
  let body;
  try { body = await response.json(); } catch { throw new Error(`서버 오류 (${response.status})`); }
  if (!response.ok) throw new Error(body.error || `서버 오류 (${response.status})`);
  return body;
}

async function refresh(preferredRow = null) {
  try {
    const suffix = `?source=${encodeURIComponent(state.source)}`;
    const [observations, summary] = await Promise.all([
      api(`/api/observations${suffix}`), api(`/api/analysis${suffix}`)
    ]);
    state.rows = observations.rows;
    state.analysis = summary.analysis;
    if (preferredRow) {
      const found = Object.entries(state.analysis.scenarios).find(([, result]) => groupMatchesRow(result, preferredRow));
      if (found) state.scenario = found[0];
    }
    const scenarioNames = Object.keys(state.analysis.scenarios);
    if (!scenarioNames.includes(state.scenario)) state.scenario = scenarioNames[0] || null;
    render();
  } catch (error) { toast(error.message, true); }
}

function render() {
  const isDemo = state.source === 'demo';
  document.querySelectorAll('[data-source]').forEach(button =>
    button.classList.toggle('active', button.dataset.source === state.source));
  $('demoBanner').hidden = !isDemo;
  $('exportLink').href = `/api/export.csv?source=${state.source}`;
  $('loadDemo').hidden = !isDemo || state.rows.length > 0;
  $('datasetMeta').textContent = `${state.rows.length}개 기록 · ${state.analysis.total_paired_runs}개 완료 페어 · ${state.analysis.noncompleted_observations}개 미완료`;
  $('emptyState').hidden = state.rows.length > 0;
  $('dashboardContent').hidden = state.rows.length === 0;
  if (!state.rows.length) return;

  const select = $('scenarioFilter');
  select.replaceChildren();
  for (const [name, result] of Object.entries(state.analysis.scenarios)) {
    const option = document.createElement('option'); option.value = name;
    option.textContent = groupLabel(result); select.append(option);
  }
  select.value = state.scenario;
  const result = state.analysis.scenarios[state.scenario];
  renderMetrics(result);
  renderBars();
  renderInsight(result);
  renderRows();
}

function renderMetrics(result) {
  const metric = result.metrics.elapsed_ms;
  if (!metric) {
    $('tossMedian').textContent = '—'; $('kisMedian').textContent = '—'; $('pairedDelta').textContent = '—';
    $('tossCount').textContent = '비교 가능한 페어 없음';
    $('kisCount').textContent = '동일 조건 기록 필요';
    $('pairNote').textContent = `${result.excluded_groups.length}개 그룹 제외`;
    return;
  }
  $('tossMedian').innerHTML = `${number(metric.toss.median, 1)}<small>ms</small>`;
  $('kisMedian').innerHTML = `${number(metric.kis.median, 1)}<small>ms</small>`;
  const difference = metric.toss_minus_kis.median;
  $('pairedDelta').innerHTML = `${difference > 0 ? '+' : ''}${number(difference, 1)}<small>ms</small>`;
  const p95Ready = metric.paired_runs >= 20 && metric.toss.p95 != null && metric.kis.p95 != null;
  $('tossCount').textContent = `${p95Ready ? `p95 ${ms(metric.toss.p95)}` : 'p95: 20쌍 미만 비표시'} · ${metric.paired_runs}회 페어`;
  $('kisCount').textContent = `${p95Ready ? `p95 ${ms(metric.kis.p95)}` : 'p95: 20쌍 미만 비표시'} · ${metric.paired_runs}회 페어`;
  const ci = metric.toss_minus_kis.bootstrap_95_ci;
  $('pairNote').textContent = ci
    ? `탐색적 95% 부트스트랩 구간 ${ms(ci[0])} ~ ${ms(ci[1])} · Toss−KIS`
    : `Toss−KIS · 구간 추정은 5쌍부터 표시`;
}

function renderBars() {
  const parent = $('scenarioBars'); parent.replaceChildren();
  const selected = state.analysis.scenarios[state.scenario];
  const scenarios = [selected];
  const maxima = scenarios.flatMap(result => {
    const metric = result.metrics.elapsed_ms;
    return metric ? [metric.toss.median, metric.kis.median] : [];
  });
  const max = Math.max(...maxima, 1) * 1.12;
  for (const result of scenarios) {
    const group = document.createElement('div'); group.className = 'bar-group';
    const heading = document.createElement('div'); heading.className = 'bar-label';
    heading.textContent = groupLabel(result); group.append(heading);
    const bars = document.createElement('div'); bars.className = 'bar-pair';
    const metric = result.metrics.elapsed_ms;
    if (metric) {
      for (const [app, text] of [['toss', '토스'], ['kis', '한투']]) {
        const line = document.createElement('div'); line.className = 'bar-line';
        const title = document.createElement('span'); title.textContent = text;
        const track = document.createElement('div'); track.className = 'bar-track';
        const fill = document.createElement('div'); fill.className = `bar-fill ${app}`;
        fill.style.width = `${metric[app].median / max * 100}%`;
        const value = document.createElement('span'); value.className = 'bar-value';
        value.textContent = ms(metric[app].median);
        track.append(fill); line.append(title, track, value); bars.append(line);
      }
    } else {
      const missing = document.createElement('div'); missing.className = 'small-caption';
      missing.textContent = '비교 가능한 페어 없음'; bars.append(missing);
    }
    group.append(bars); parent.append(group);
  }
}

function renderInsight(result) {
  const parent = $('insightBody'); parent.replaceChildren(); parent.className = 'insight-body';
  const highlight = document.createElement('div'); highlight.className = 'insight-highlight';
  const heading = document.createElement('strong');
  const explanation = document.createElement('p');
  const metric = result.metrics.elapsed_ms;
  if (!metric) {
    heading.textContent = '완료된 비교 페어가 없습니다';
    explanation.textContent = '같은 비교 조건·run ID·기기·네트워크에서 두 앱의 완료 기록이 한 개씩 필요합니다.';
  } else {
    const delta = metric.toss_minus_kis.median;
    heading.textContent = delta < 0 ? '이 표본에서 토스의 시간이 낮음' : delta > 0 ? '이 표본에서 한투의 시간이 낮음' : '이 표본에서 중앙 차이 0 ms';
    explanation.textContent = `완전한 페어 ${metric.paired_runs}개에서 차이 중앙값은 ${delta > 0 ? '+' : ''}${ms(delta)}입니다. 원인이나 모든 사용자에 대한 우열은 뜻하지 않습니다.`;
  }
  highlight.append(heading, explanation); parent.append(highlight);
  const phases = [
    ['first_response_ms', '첫 반응', 'ms'],
    ['content_ms', '콘텐츠 구간', 'ms'],
    ['frame_jank_pct', '프레임 끊김', '%'],
    ['network_rtt_ms', '네트워크 RTT', 'ms']
  ].filter(([key]) => result.metrics[key]);
  if (phases.length) {
    const title = document.createElement('div'); title.className = 'phase-title';
    title.textContent = '보조 지표 · 각 지표의 완전한 페어만'; parent.append(title);
    const grid = document.createElement('div'); grid.className = 'phase-grid';
    for (const [key, label, unit] of phases) {
      const item = document.createElement('div'); item.className = 'phase-item';
      const name = document.createElement('span'); name.textContent = label;
      const values = document.createElement('strong');
      const phase = result.metrics[key];
      values.textContent = `토스 ${number(phase.toss.median, 1)}${unit} · 한투 ${number(phase.kis.median, 1)}${unit}`;
      item.append(name, values); grid.append(item);
    }
    parent.append(grid);
  }
  const list = document.createElement('ul'); list.className = 'insight-list';
  const items = [
    `토스: 완료 ${result.status_counts.toss.completed} · 시간 초과 ${result.status_counts.toss.timeout} · 오류 ${result.status_counts.toss.error} / 한투: 완료 ${result.status_counts.kis.completed} · 시간 초과 ${result.status_counts.kis.timeout} · 오류 ${result.status_counts.kis.error}`,
    `${result.excluded_groups.length}개 페어 그룹 제외 · 짝 없음/중복/미완료 확인. 다른 종목·키보드 상태·기기·네트워크·판독 방식은 별도 그룹입니다.`,
    !metric ? '두 앱의 완료 페어를 만든 뒤 앱 순서를 교차하며 조건당 20~30쌍 이상 측정하세요.'
      : metric.paired_runs < 20 ? '반복 횟수가 적습니다. 앱 순서를 교차하며 조건당 20~30쌍 이상 측정하세요.'
        : '반복 측정과 실행 순서 기록을 함께 검토하세요.',
    '전송 관찰 메모는 사용자가 입력한 값이며 프로토콜이나 인과를 자동 판정하지 않습니다.'
  ];
  if (result.timing_basis === 'playback_seek') items.unshift('브라우저 재생 위치로 읽은 근사값입니다. 프레임 번호나 PTS로 다시 판독한 결과와 섞지 않았습니다.');
  if (result.timing_basis === 'unspecified') items.unshift('시간 판독 근거가 없는 이전 기록입니다. 새 실측 결과와 섞지 않았습니다.');
  if (result.stock_code === 'unspecified') items.unshift('종목 코드가 미기록된 그룹입니다. 코드가 기록된 실측 결과와 섞지 않았습니다.');
  if (result.keyboard_state === 'unspecified') items.unshift('시작 시점의 키보드 상태가 미기록된 그룹입니다. 상태가 기록된 실측 결과와 섞지 않았습니다.');
  const quality = result.quality_checks;
  if (quality) {
    const order = quality.run_order;
    items.push(`완료 페어 실행 순서: 토스 선행 ${order.toss_first} · 한국투자 선행 ${order.kis_first} · 미기록 ${order.unrecorded} · 순서 중복 ${order.duplicate}`);
    for (const [app, label] of [['toss', '토스'], ['kis', '한국투자']]) {
      const versions = quality.app_versions[app];
      const counts = Object.entries(versions.counts).map(([version, count]) => `${version} ${count}회`).join(', ');
      items.push(`${label} 앱 버전: ${counts || '미기록'}${versions.missing ? ` · 버전 미기록 ${versions.missing}회` : ''}`);
      if (versions.mixed) items.push(`${label} 앱의 서로 다른 버전이 섞였습니다. 버전별로 결과를 다시 검토하세요.`);
    }
    if (order.duplicate) items.push('같은 페어에서 두 앱의 실행 순서가 중복된 기록이 있습니다. 원자료를 확인하세요.');
  }
  for (const item of items) { const li = document.createElement('li'); li.textContent = item; list.append(li); }
  parent.append(list);
  if (result.excluded_groups.length) {
    const details = document.createElement('details'); details.className = 'excluded-details';
    const summary = document.createElement('summary'); summary.textContent = '제외된 실행 상세 보기'; details.append(summary);
    const excluded = document.createElement('ul');
    for (const group of result.excluded_groups.slice(0, 40)) {
      const item = document.createElement('li');
      item.textContent = `${group.run_id}: ${exclusionLabels[group.reason] || group.reason}`;
      excluded.append(item);
    }
    details.append(excluded); parent.append(details);
  }
}

function renderRows() {
  const selected = state.analysis.scenarios[state.scenario];
  const rows = state.rows.filter(row => groupMatchesRow(selected, {
    ...row,
    capture_method: row.capture_method || 'unspecified',
    stock_code: row.stock_code || 'unspecified',
    keyboard_state: row.keyboard_state || 'unspecified',
    start_state: row.start_state || 'unspecified',
    market_phase: row.market_phase || 'unspecified',
    timing_basis: row.timing_basis || 'unspecified',
    completion_criterion: row.completion_criterion || 'unspecified'
  }));
  $('rowCount').textContent = `${rows.length}개 기록`;
  const tbody = $('recordsTable'); tbody.replaceChildren();
  for (const row of rows.slice(0, 200)) {
    const tr = document.createElement('tr');
    const first = document.createElement('td');
    const main = document.createElement('div'); main.className = 'row-main'; main.textContent = scenarioLabel(row.scenario);
    const sub = document.createElement('div'); sub.className = 'row-sub';
    const timingEvidence = row.timing_basis === 'fixed_fps_frames'
      ? ` · 프레임 ${row.start_frame}→${row.end_frame}`
      : row.timing_basis === 'frame_timestamps'
        ? `${row.start_frame != null && row.end_frame != null ? ` · 프레임 ${row.start_frame}→${row.end_frame}` : ''} · PTS ${number(row.start_pts_ms, 3)}→${number(row.end_pts_ms, 3)}ms`
        : '';
    sub.textContent = `${row.run_id} · ${stockLabel(row.stock_code || 'unspecified')} · ${keyboardLabels[row.keyboard_state || 'unspecified']} · ${row.device} · ${row.network_type}${row.run_order ? ` · 순서 ${row.run_order}` : ''}${row.app_version ? ` · v${row.app_version}` : ''}${timingEvidence}`;
    first.append(main, sub); tr.append(first);
    const app = document.createElement('td'); const tag = document.createElement('span');
    tag.className = `app-tag ${row.app}`; tag.textContent = row.app === 'toss' ? 'TOSS' : 'KIS';
    app.append(tag); tr.append(app);
    const status = document.createElement('td');
    const statusTag = document.createElement('span'); statusTag.className = `status-tag ${row.status || 'completed'}`;
    statusTag.textContent = statusLabels[row.status || 'completed'] || row.status;
    status.append(statusTag); tr.append(status);
    for (const value of [ms(row.elapsed_ms), ms(row.first_response_ms), row.frame_jank_pct == null ? '—' : `${number(row.frame_jank_pct, 1)}%`, row.protocol_observation || '미기록']) {
      const cell = document.createElement('td'); cell.textContent = value; tr.append(cell);
    }
    const date = document.createElement('td'); date.textContent = row.timestamp ? new Date(row.timestamp).toLocaleString('ko-KR') : '—'; tr.append(date);
    const action = document.createElement('td'); const remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'delete-row'; remove.textContent = '×'; remove.title = '이 기록 삭제';
    remove.addEventListener('click', async () => {
      if (!window.confirm('이 관찰값을 삭제할까요?')) return;
      try { await api(`/api/observations/${row.id}`, { method: 'DELETE' }); await refresh(); toast('기록을 삭제했습니다.'); }
      catch (error) { toast(error.message, true); }
    });
    action.append(remove); tr.append(action); tbody.append(tr);
  }
}

document.querySelectorAll('[data-source]').forEach(button => button.addEventListener('click', () => {
  state.source = button.dataset.source; state.scenario = null; refresh();
}));
$('scenarioFilter').addEventListener('change', event => { state.scenario = event.target.value; render(); });
$('loadDemo').addEventListener('click', async () => {
  try { await api('/api/demo', { method: 'POST' }); await refresh(); toast('가상 예시 데이터를 만들었습니다.'); }
  catch (error) { toast(error.message, true); }
});
const criterionDefaults = {
  search_result_to_detail_quote: '선택 종목명과 0·빈칸·이전 종목 잔상이 아닌 가격 숫자가 함께 읽힘',
  stock_search: '검색 결과에서 동일 종목이 표시됨',
  quote_detail: '종목명과 가격이 모두 표시됨',
  chart_open: '차트의 첫 데이터가 표시됨',
  app_launch: '증권 홈의 핵심 데이터가 표시됨',
  cold_launch: '증권 홈의 핵심 데이터가 표시됨',
  warm_navigation: '대상 화면의 핵심 데이터가 표시됨'
};
const recordForm = $('recordForm');
let previousTimingBasis = recordForm.elements.timing_basis.value;
function updateStockCondition() {
  const stockScenario = recordForm.elements.scenario.value !== 'app_launch';
  const code = recordForm.elements.stock_code;
  const keyboard = recordForm.elements.keyboard_state;
  code.required = stockScenario;
  code.pattern = stockScenario ? '[0-9]{6}' : '[0-9]{6}|not_applicable';
  if (stockScenario && code.value === 'not_applicable') code.value = '';
  if (!stockScenario && !code.value) code.value = 'not_applicable';
  const noKeyboard = keyboard.querySelector('option[value="not_applicable"]');
  noKeyboard.disabled = stockScenario;
  if (stockScenario && keyboard.value === 'not_applicable') keyboard.value = '';
}
function updateElapsedLabel() {
  const completed = recordForm.elements.status.value === 'completed';
  const derived = ['fixed_fps_frames', 'frame_timestamps'].includes(recordForm.elements.timing_basis.value);
  $('elapsedLabel').textContent = `${completed ? '탭 → 콘텐츠 표시' : '탭 → 오류/제한 시점'} (ms)${derived ? ' · 자동 계산' : ' *'}`;
  $('elapsedInput').required = !derived;
}
function updatePtsFramePairRequired() {
  const basis = recordForm.elements.timing_basis.value;
  const start = recordForm.elements.start_frame;
  const end = recordForm.elements.end_frame;
  start.required = basis === 'fixed_fps_frames' || (basis === 'frame_timestamps' && Boolean(end.value));
  end.required = basis === 'fixed_fps_frames' || (basis === 'frame_timestamps' && Boolean(start.value));
}
function updateTimingEvidence() {
  const basis = recordForm.elements.timing_basis.value;
  const fixed = basis === 'fixed_fps_frames';
  const pts = basis === 'frame_timestamps';
  if (basis !== previousTimingBasis) $('elapsedInput').value = '';
  if (basis !== previousTimingBasis && (fixed || pts)) {
    recordForm.elements.start_frame.value = '';
    recordForm.elements.end_frame.value = '';
  }
  previousTimingBasis = basis;
  $('frameEvidence').hidden = !(fixed || pts);
  $('ptsEvidence').hidden = !pts;
  for (const name of ['start_frame', 'end_frame']) {
    recordForm.elements[name].min = pts ? '1' : '0';
    if (!fixed && !pts) recordForm.elements[name].value = '';
  }
  updatePtsFramePairRequired();
  for (const name of ['start_pts_ms', 'end_pts_ms']) {
    recordForm.elements[name].required = pts;
    if (!pts) recordForm.elements[name].value = '';
  }
  recordForm.elements.capture_fps.required = fixed;
  recordForm.elements.capture_fps.disabled = !fixed;
  if (!fixed) recordForm.elements.capture_fps.value = '';
  updateElapsedLabel();
}
recordForm.elements.timing_basis.addEventListener('change', updateTimingEvidence);
for (const name of ['start_frame', 'end_frame']) {
  recordForm.elements[name].addEventListener('input', updatePtsFramePairRequired);
}
updateTimingEvidence();
updateStockCondition();
function applyTimingBasis(basis) {
  recordForm.elements.timing_basis.value = basis;
  updateTimingEvidence();
}
recordForm.elements.scenario.addEventListener('change', event => {
  updateStockCondition();
  const criterion = recordForm.elements.completion_criterion;
  if (!criterion.value || Object.values(criterionDefaults).includes(criterion.value)) {
    criterion.value = criterionDefaults[event.target.value] || '';
  }
});
recordForm.elements.status.addEventListener('change', event => {
  const completed = event.target.value === 'completed';
  updateElapsedLabel();
  recordForm.elements.content_ms.disabled = !completed;
  if (!completed) recordForm.elements.content_ms.value = '';
});
$('recordForm').addEventListener('submit', async event => {
  event.preventDefault();
  const form = event.currentTarget;
  const row = Object.fromEntries(new FormData(form).entries());
  const prepareCounterpart = event.submitter?.id === 'savePairNext';
  try {
    await api('/api/observations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(row) });
    state.source = 'real';
    form.reset(); recordForm.elements.content_ms.disabled = false; updateTimingEvidence(); updateStockCondition();
    updateElapsedLabel();
    clearVideoMeasurementTools();
    await refresh(row);
    if (prepareCounterpart) {
      const sameGroup = state.rows.some(existing => existing.run_id === row.run_id &&
        existing.app !== row.app && groupMatchesRow(existing, row));
      if (!sameGroup && !['2', '4'].includes(row.run_order)) {
        for (const name of ['scenario', 'stock_code', 'keyboard_state', 'capture_method', 'start_state', 'market_phase',
                            'completion_criterion', 'run_id', 'network_type', 'device']) {
          form.elements[name].value = row[name];
        }
        updateStockCondition();
        form.elements.app.value = row.app === 'toss' ? 'kis' : 'toss';
        form.elements.timing_basis.value = row.timing_basis;
        updateTimingEvidence();
        if (row.timing_basis === 'fixed_fps_frames') form.elements.capture_fps.value = row.capture_fps;
        const otherOrder = { '1': '2', '3': '4' }[row.run_order];
        if (otherOrder) form.elements.run_order.value = otherOrder;
        toast('첫 기록을 저장했습니다. 같은 조건과 run ID로 반대 앱을 준비했습니다. 새 영상 시점을 입력하세요.');
      } else if (['2', '4'].includes(row.run_order)) {
        toast('기록을 저장했습니다. 두 번째 시도이므로 새 페어의 run ID를 입력하세요.');
      } else {
        toast('기록을 저장했습니다. 이 run ID에는 반대 앱 기록이 이미 있어 폼을 초기화했습니다.');
      }
    } else {
      toast('측정값을 저장했습니다.');
    }
  } catch (error) { toast(error.message, true); }
});
$('csvFile').addEventListener('change', async event => {
  const file = event.target.files[0]; if (!file) return;
  $('csvName').textContent = file.name;
  try {
    if (file.size > 2_000_000) throw new Error('CSV는 2 MB 이하여야 합니다.');
    const csv = await file.text();
    const result = await api('/api/import', { method: 'POST', headers: { 'Content-Type': 'text/csv; charset=utf-8' }, body: csv });
    state.source = 'real'; state.scenario = null; await refresh();
    toast(`${result.count}개 측정값을 가져왔습니다.`);
  } catch (error) { toast(error.message, true); }
  event.target.value = '';
});

$('videoFile').addEventListener('change', event => {
  const file = event.target.files[0]; if (!file) return;
  if (state.videoUrl) URL.revokeObjectURL(state.videoUrl);
  state.videoUrl = URL.createObjectURL(file);
  state.tapAt = state.contentAt = null;
  const video = $('videoPreview'); video.src = state.videoUrl; video.hidden = false;
  $('videoControls').hidden = false; updateVideoMarks();
});
function clearVideoMeasurementTools() {
  $('tapFrame').value = '';
  $('contentFrame').value = '';
  $('startPtsSeconds').value = '';
  $('endPtsSeconds').value = '';
  updateFrameMeasurement();
  updatePtsMeasurement();
  if (state.videoUrl) URL.revokeObjectURL(state.videoUrl);
  state.videoUrl = null;
  state.tapAt = state.contentAt = null;
  const video = $('videoPreview');
  video.pause(); video.removeAttribute('src'); video.load(); video.hidden = true;
  $('videoFile').value = '';
  $('videoControls').hidden = true;
  updateVideoMarks();
}
$('videoPreview').addEventListener('timeupdate', () => $('currentTime').textContent = `${$('videoPreview').currentTime.toFixed(3)} s`);
$('videoPreview').addEventListener('loadedmetadata', () => $('videoDuration').textContent = `길이 ${$('videoPreview').duration.toFixed(3)} s`);
$('markTap').addEventListener('click', () => { state.tapAt = $('videoPreview').currentTime; updateVideoMarks(); });
$('markContent').addEventListener('click', () => { state.contentAt = $('videoPreview').currentTime; updateVideoMarks(); });
function updateVideoMarks() {
  $('tapTime').textContent = `탭: ${state.tapAt == null ? '—' : state.tapAt.toFixed(3) + ' s'}`;
  $('contentTime').textContent = `콘텐츠: ${state.contentAt == null ? '—' : state.contentAt.toFixed(3) + ' s'}`;
  const valid = state.tapAt != null && state.contentAt != null && state.contentAt > state.tapAt;
  $('videoResult').textContent = valid ? `탭 → 콘텐츠 표시: 약 ${number((state.contentAt - state.tapAt) * 1000)} ms · 재생 위치 기준, 프레임 확인 필요` : '두 시점을 순서대로 표시하면 시간 차이가 나옵니다.';
  $('applyVideoTime').disabled = !valid;
}
$('applyVideoTime').addEventListener('click', () => {
  applyTimingBasis('playback_seek');
  $('elapsedInput').value = ((state.contentAt - state.tapAt) * 1000).toFixed(0);
  $('record').scrollIntoView({ behavior: 'smooth' }); toast('재생 위치 근사치를 기록 폼에 넣었습니다. 프레임 판독값과 별도 그룹으로 분석됩니다.');
});

function frameMeasurement() {
  const fps = Number($('frameFps').value);
  const start = Number($('tapFrame').value);
  const end = Number($('contentFrame').value);
  if (!$('frameFps').value || !$('tapFrame').value || !$('contentFrame').value ||
      !Number.isFinite(fps) || fps <= 0 || fps > 1000 ||
      !Number.isSafeInteger(start) || !Number.isSafeInteger(end) ||
      start < 0 || end <= start) return null;
  const elapsed = (end - start) / fps * 1000;
  return elapsed <= 300000 ? { fps, start, end, elapsed } : null;
}
function updateFrameMeasurement() {
  const measurement = frameMeasurement();
  $('applyFrameTime').disabled = !measurement;
  $('frameResult').textContent = measurement
    ? `탭 → 콘텐츠 표시: ${number(measurement.elapsed, 1)} ms · ${measurement.end - measurement.start}프레임 · 1프레임 ${number(1000 / measurement.fps, 1)} ms`
    : '고정 FPS와 서로 다른 두 프레임 번호를 입력하세요.';
}
for (const id of ['frameFps', 'tapFrame', 'contentFrame']) {
  $(id).addEventListener('input', updateFrameMeasurement);
}
$('applyFrameTime').addEventListener('click', () => {
  const measurement = frameMeasurement();
  if (!measurement) return;
  applyTimingBasis('fixed_fps_frames');
  $('elapsedInput').value = measurement.elapsed.toFixed(3);
  recordForm.elements.capture_fps.value = measurement.fps;
  recordForm.elements.start_frame.value = measurement.start;
  recordForm.elements.end_frame.value = measurement.end;
  $('record').scrollIntoView({ behavior: 'smooth' });
  toast('계산값·FPS·시작/끝 프레임을 기록 폼에 넣었습니다. 촬영 방식도 선택하세요.');
});

function ptsMeasurement() {
  const start = Number($('startPtsSeconds').value);
  const end = Number($('endPtsSeconds').value);
  if (!$('startPtsSeconds').value || !$('endPtsSeconds').value ||
      !Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end <= start) return null;
  const elapsed = (end - start) * 1000;
  return elapsed > 0 && elapsed <= 300000 ? { start, end, elapsed } : null;
}
function updatePtsMeasurement() {
  const measurement = ptsMeasurement();
  $('applyPtsTime').disabled = !measurement;
  $('ptsResult').textContent = measurement
    ? `탭 → 콘텐츠 표시: ${number(measurement.elapsed, 3)} ms · PTS 차이`
    : '서로 다른 두 프레임의 표시 시각(초)을 입력하세요.';
}
for (const id of ['startPtsSeconds', 'endPtsSeconds']) {
  $(id).addEventListener('input', updatePtsMeasurement);
}
$('applyPtsTime').addEventListener('click', () => {
  const measurement = ptsMeasurement();
  if (!measurement) return;
  applyTimingBasis('frame_timestamps');
  recordForm.elements.start_frame.value = '';
  recordForm.elements.end_frame.value = '';
  updatePtsFramePairRequired();
  recordForm.elements.start_pts_ms.value = (measurement.start * 1000).toFixed(3);
  recordForm.elements.end_pts_ms.value = (measurement.end * 1000).toFixed(3);
  $('elapsedInput').value = measurement.elapsed.toFixed(3);
  $('record').scrollIntoView({ behavior: 'smooth' });
  toast('PTS 계산값을 넣었습니다. 원본 PNG 번호가 있다면 시작/끝 프레임도 입력하세요.');
});
refresh();
