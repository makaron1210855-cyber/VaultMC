/* Индикатор связи с другом во время игры (у гостя): способ подключения и задержка.
 * Значок в верхней полосе: зелёный — до 100 мс, жёлтый — до 250 мс, красный — больше или связь прервалась.
 * Задержку меряет: ретранслятор — лаунчер (core/relay.py, раз в 10 с), напрямую — браузер (p2p.js, раз в 5 с).
 * Выбор пути: перед входом меряем ретранслятор и e4mc и идём через быстрый (pickFastest). Во время игры раз в 20 с
 * меряем и другие пути (alts из rooms.js); если текущий хуже на 100+ мс целую минуту — спрашиваем, переключиться ли.
 * Сами не переключаем: переход — это выйти из мира и зайти снова.
 */
const NET = { mode: '', timer: 0, address: '', alts: [], hist: {}, since: 0, ticks: 0, asked: false, probing: false };
const PATH_TIE_MS = 20;          // разница меньше — погрешность замера, пути равны
const SWITCH_GAP_MS = 100;       // насколько текущий путь должен быть хуже, чтобы предложить другой
const SWITCH_WINDOW_MS = 60000;  // ...и сколько времени подряд
const ALT_PROBE_TICKS = 4;       // другие пути меряем раз в 4 тика по 5 с = раз в 20 с

/* Самый быстрый путь из списка (в порядке прежнего приоритета). Следующий путь выигрывает,
   только если быстрее больше чем на PATH_TIE_MS, иначе остаётся прежний. ms = null — путь не ответил. */
function pickFastest(cands) {
  let best = null;
  for (const c of cands) {
    if (c.ms == null) continue;
    if (!best || c.ms < best.ms - PATH_TIE_MS) best = c;
  }
  return best;
}

/* Замерить несколько путей сразу; каждый замер — не дольше timeoutMs. Возвращает мс или null по каждому. */
function measurePaths(probes, timeoutMs = 6000) {
  const one = (probe) => Promise.race([
    Promise.resolve().then(probe).then((r) => (r && r.ms != null ? r.ms : null)).catch(() => null),
    new Promise((resolve) => setTimeout(() => resolve(null), timeoutMs)),
  ]);
  return Promise.all(probes.map(one));
}

const netMedian = (xs) => { const a = [...xs].sort((x, y) => x - y); return a.length ? a[Math.floor(a.length / 2)] : null; };

/* Нужно ли предложить другой путь. hist: {путь: [{t, ms}]}. Прямое соединение меряется до самого друга, а ретранслятор
   и e4mc — только до своего сервера (половина пути), поэтому против прямого их задержку считаем вдвое больше.
   Возвращает {kind, cur, alt} или null. */
function netSwitchAdvice(mode, hist, kinds, since, now) {
  if (now - since < SWITCH_WINDOW_MS) return null;
  const recent = (k) => (hist[k] || []).filter((x) => now - x.t <= SWITCH_WINDOW_MS).map((x) => x.ms);
  const cur = recent(mode);
  if (cur.length < 6) return null;                     // из 12 возможных за минуту — меньше половины, судить рано
  const curMs = netMedian(cur);
  let best = null;
  for (const k of kinds) {
    const xs = recent(k);
    if (xs.length < 2) continue;
    const altMs = netMedian(xs) * (mode === 'p2p' ? 2 : 1);
    if (curMs - altMs >= SWITCH_GAP_MS && (!best || altMs < best.alt)) best = { kind: k, cur: curMs, alt: altMs };
  }
  return best;
}
const NET_LABEL = { relay: 'через ретранслятор VaultMC', p2p: 'напрямую (WebRTC)', e4mc: 'через e4mc' };

function netWatch(mode, address = '', alts = []) {
  NET.mode = mode;
  NET.address = address;
  NET.alts = alts;
  NET.hist = {};
  NET.since = Date.now();
  NET.ticks = 0;
  NET.asked = false;
  clearInterval(NET.timer);
  NET.timer = setInterval(netTick, 5000);
  netTick();
}

function netStop() {
  NET.mode = '';
  NET.address = '';
  NET.alts = [];
  NET.hist = {};
  clearInterval(NET.timer);
  NET.timer = 0;
  netPaint(null);
}

async function netTick() {
  if (!NET.mode) return;
  let rtt = null, problem = '';
  if (NET.mode === 'p2p') {
    const g = P2P.guest;
    if (!g) return netPaint(null);
    rtt = g.stat.rtt ?? null;
    if (g.lost) problem = 'связь прервалась';
  } else if (NET.mode === 'relay') {
    const r = await api.net_status().catch(() => null);
    if (!r || !r.mode) return netPaint(null);
    rtt = r.rtt ?? null;
    problem = r.error || (rtt === null ? 'ретранслятор не отвечает' : '');
  } else if (NET.mode === 'e4mc') {
    // e4mc пересылает трафик через свой сервер: замер TCP до его адреса — приближение задержки до друга
    const r = await api.net_ping(NET.address).catch(() => null);
    rtt = r && r.ms != null ? r.ms : null;
    problem = rtt === null ? 'адрес не отвечает' : '';
  }
  netPaint({ mode: NET.mode, rtt, problem });
  if (S.state !== 'running') return;
  if (rtt !== null && !problem) netRemember(NET.mode, rtt);
  if (NET.alts.length && ++NET.ticks % ALT_PROBE_TICKS === 0) netProbeAlts();
  netConsider();
}

function netRemember(kind, ms) {
  const now = Date.now();
  NET.hist[kind] = [...(NET.hist[kind] || []).filter((x) => now - x.t <= SWITCH_WINDOW_MS * 2), { t: now, ms }];
}

async function netProbeAlts() {
  if (NET.probing) return;
  NET.probing = true;
  const mode = NET.mode, alts = NET.alts;
  try {
    const got = await measurePaths(alts.map((a) => a.probe));
    if (NET.mode !== mode) return;                     // пока мерили, путь сменился
    got.forEach((ms, i) => { if (ms !== null) netRemember(alts[i].kind, ms); });
  } finally {
    NET.probing = false;
  }
}

function netConsider() {
  if (NET.asked || !NET.alts.length || S.state !== 'running') return;
  if ($('#modalRoot')?.classList.contains('open')) return;   // не перебиваем другое окно — спросим на следующем замере
  const advice = netSwitchAdvice(NET.mode, NET.hist, NET.alts.map((a) => a.kind), NET.since, Date.now());
  const alt = advice && NET.alts.find((a) => a.kind === advice.kind);
  if (alt) netOffer(alt, advice);
}

/* Окно «Переключиться / Остаться». Спрашиваем один раз за путь: после «Остаться» не надоедаем до следующего входа. */
function netOffer(alt, { cur, alt: altMs }) {
  NET.asked = true;
  const from = NET_LABEL[NET.mode] || '', to = NET_LABEL[alt.kind] || '';
  if (typeof sysNotify === 'function') sysNotify('net-switch', 'Можно подключиться к другу быстрее', `Сейчас задержка ${cur} мс, ${to} — около ${altMs} мс. Откройте лаунчер`);
  const m = openModal(`<h3>${ic('refresh')} Можно подключиться к другу быстрее</h3>
    <p>Сейчас связь идёт ${esc(from)}: задержка около <b class="no-tr">${cur} мс</b> уже минуту.</p>
    <p>Другой путь — ${esc(to)}: около <b class="no-tr">${altMs} мс</b>.</p>
    <p class="small muted">Переключение прервёт игру на несколько секунд: после него нужно выйти из мира и зайти снова через «Сетевая игра». Лаунчер сам ничего не переключит.</p>
    <div class="foot"><button class="btn ghost" data-close>Остаться</button><button class="btn primary" id="netSwitch">Переключиться</button></div>`);
  $('#netSwitch', m).onclick = async () => {
    closeModal();
    try { await alt.switchTo(); } catch (e) { toast(String(e && e.message || e), 'error', 9000); }
  };
}

function netPaint(s) {
  const chip = $('#netChip');
  if (!chip) return;
  if (!s || S.state !== 'running') { chip.classList.remove('on'); return; }
  const level = s.problem ? 'bad' : s.rtt === null ? 'unknown' : s.rtt < 100 ? 'good' : s.rtt < 250 ? 'mid' : 'bad';
  chip.className = `net-chip on ${level}`;
  chip.innerHTML = `<i></i><span class="no-tr">${s.rtt !== null ? s.rtt + ' мс' : '—'}</span>`;
  chip.title = `Связь с другом: ${NET_LABEL[s.mode] || ''}${s.rtt !== null ? `, задержка ${s.rtt} мс` : ''}${s.problem ? ` — ${s.problem}` : ''}`;
}

onEvent('game_exit', netStop);
onEvent('state', () => netTick());
