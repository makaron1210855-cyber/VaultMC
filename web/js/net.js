/* Индикатор связи с другом во время игры (у гостя): способ подключения и задержка.
 * Значок в верхней полосе: зелёный — до 100 мс, жёлтый — до 250 мс, красный — больше или связь прервалась.
 * Задержку меряет: ретранслятор — лаунчер (core/relay.py, раз в 10 с), напрямую — браузер (p2p.js, раз в 5 с).
 */
const NET = { mode: '', timer: 0 };
const NET_LABEL = { relay: 'через ретранслятор VaultMC', p2p: 'напрямую (WebRTC)', e4mc: 'через e4mc' };

function netWatch(mode) {
  NET.mode = mode;
  clearInterval(NET.timer);
  NET.timer = setInterval(netTick, 5000);
  netTick();
}

function netStop() {
  NET.mode = '';
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
  }
  netPaint({ mode: NET.mode, rtt, problem });
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
