/* Прямое подключение к другу (как в XMCL): WebRTC-канал между окнами лаунчеров.
 * Байты игры идут: игра ⇄ мост в Python (127.0.0.1) ⇄ WebSocket ⇄ этот файл ⇄ RTCDataChannel ⇄ друг.
 * Сервер VaultMC нужен только чтобы обменяться «приглашениями» (SDP); сами данные идут напрямую.
 * Порядок для гостя (rooms.js joinFriend): ретранслятор VaultMC → этот способ → туннель e4mc.
 */
const P2P_ICE = [{ urls: ['stun:stun.l.google.com:19302', 'stun:stun1.l.google.com:19302'] }];
const P2P = { host: null, guest: null };

// STUN + ретранслятор TURN (Cloudflare, данные выдаёт сервер VaultMC на 2 часа): без него за строгим NAT прямого пути нет
let p2pIceCache = { at: 0, list: P2P_ICE };
async function p2pIce() {
  if (Date.now() - p2pIceCache.at < 3600000) return p2pIceCache.list;
  const r = await safe(api.p2p_ice());
  const extra = (r && r.servers) || [];
  // пустой ответ (сервер моргнул) запоминаем ненадолго, чтобы через минуту попробовать снова
  p2pIceCache = { at: extra.length ? Date.now() : Date.now() - 3540000, list: P2P_ICE.concat(extra) };
  return p2pIceCache.list;
}

function p2pId() {
  const a = crypto.getRandomValues(new Uint8Array(8));
  return [...a].map((b) => (b % 36).toString(36)).join('') + Date.now().toString(36).slice(-4);
}

// ждём, пока браузер соберёт свои адреса (без «trickle ICE» — приглашение отправляем одним куском)
function p2pGathered(pc, ms = 6000) {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const done = () => { clearTimeout(t); pc.removeEventListener('icegatheringstatechange', check); resolve(); };
    const check = () => { if (pc.iceGatheringState === 'complete') done(); };
    const t = setTimeout(done, ms);
    pc.addEventListener('icegatheringstatechange', check);
  });
}

// канал WebRTC ⇄ WebSocket к мосту. Очередь — пока одна из сторон ещё открывается.
function p2pPipe(dc, wsUrl, stat = { rx: 0, tx: 0, q: 0, buf: 0 }) {
  dc.binaryType = 'arraybuffer';
  const ws = new WebSocket(wsUrl);
  ws.binaryType = 'arraybuffer';
  const toWs = [], toDc = [];
  const flushWs = () => { while (toWs.length && ws.readyState === 1) ws.send(toWs.shift()); };
  // В канал WebRTC не кладём больше ~4 МБ разом: при переполнении (16 МБ) браузер закрывает канал, а кусок данных
  // терялся — игра у друга отключалась при загрузке мира. Остальное ждёт в очереди до onbufferedamountlow.
  // Скорость отправки ограничена: замеры 16.11 показали, что мир, отправленный разом (3 МБ за 5 с), забивает канал —
  // потом он еле ползёт (2 КБ/с), и игра вылетает по тайм-ауту. Ровная отправка не даёт этому случиться.
  const RATE = 600 * 1024;                    // байт в секунду
  let nextAt = 0, timer = 0;
  const flushDc = () => {
    if (timer) return;
    while (toDc.length && dc.readyState === 'open' && dc.bufferedAmount < (4 << 20)) {
      const now = performance.now();
      if (now < nextAt) { timer = setTimeout(() => { timer = 0; flushDc(); }, nextAt - now); return; }
      try { dc.send(toDc[0]); } catch (e) { return; }
      const size = toDc[0].byteLength || 0;
      stat.tx += size;
      toDc.shift();
      nextAt = Math.max(now, nextAt) + (size / RATE) * 1000;
    }
  };
  dc.onmessage = (e) => { stat.rx += e.data.byteLength || 0; toWs.push(e.data); flushWs(); };
  ws.onmessage = (e) => { toDc.push(e.data); stat.q = Math.max(stat.q, toDc.length); flushDc(); stat.buf = Math.max(stat.buf, dc.bufferedAmount); };
  ws.onopen = flushWs;
  dc.onopen = flushDc;
  dc.bufferedAmountLowThreshold = 1 << 20;
  dc.onbufferedamountlow = flushDc;
  const close = () => { try { ws.close(); } catch (e) { /* уже */ } try { dc.close(); } catch (e) { /* уже */ } };
  ws.onclose = close; ws.onerror = close;
  dc.onclose = close; dc.onerror = close;
}


// i18n-skip-start: две функции ниже пишут только в консоль для разработчика, интерфейс их текст не показывает
/** Сколько адресов каждого типа собрал браузер (relay = достучались до ретранслятора Cloudflare) + смена состояний ICE. */
function p2pIceDiag(pc, label) {
  const log = (t) => safe(api.p2p_log(`${label}: ${t}`));
  const count = () => {
    const sdp = (pc.localDescription && pc.localDescription.sdp) || '';
    const n = { host: 0, srflx: 0, relay: 0 };
    (sdp.match(/ typ (host|srflx|relay)/g) || []).forEach((m) => { n[m.trim().split(' ')[1]]++; });
    return `адресов: свои ${n.host}, внешние ${n.srflx}, через Cloudflare ${n.relay}`;
  };
  pc.addEventListener('icegatheringstatechange', () => { if (pc.iceGatheringState === 'complete') log(count()); });
  pc.addEventListener('iceconnectionstatechange', () => log(`ICE: ${pc.iceConnectionState}`));
  return count;
}

/** Пишет в консоль лаунчера, каким путём идёт соединение (напрямую / через ретранслятор) и сколько данных проходит. */
function p2pMonitor(pc, label, stat) {
  const log = (t) => safe(api.p2p_log(`${label}: ${t}`));
  let told = false, last = { rx: 0, tx: 0 }, tick = 0;
  const iv = setInterval(async () => {
    const cs = pc.connectionState;
    if (cs === 'closed' || cs === 'failed') {
      clearInterval(iv); log(`соединение ${cs === 'closed' ? 'закрыто' : 'оборвалось'} (передано ${Math.round(stat.tx / 1024)} КБ, получено ${Math.round(stat.rx / 1024)} КБ)`);
      return;
    }
    if (cs !== 'connected') return;
    let path = '', rtt = '';
    try {
      const rep = await pc.getStats(); let sel = null;
      rep.forEach((r) => { if (r.type === 'transport' && r.selectedCandidatePairId) sel = rep.get(r.selectedCandidatePairId); });
      if (!sel) rep.forEach((r) => { if (r.type === 'candidate-pair' && r.nominated && r.state === 'succeeded') sel = r; });
      if (sel) {
        const l = rep.get(sel.localCandidateId), rm = rep.get(sel.remoteCandidateId);
        path = `${(l && l.candidateType) || '?'}→${(rm && rm.candidateType) || '?'}`;
        if (sel.currentRoundTripTime != null) {
          stat.rtt = Math.round(sel.currentRoundTripTime * 1000);   // для индикатора связи (web/js/net.js)
          rtt = `${stat.rtt} мс`;
        }
      }
    } catch (e) { /* статистика недоступна */ }
    if (!told) { told = true; log(`путь ${path || 'неизвестен'} (relay = через ретранслятор Cloudflare, host/srflx = напрямую)${rtt ? ', задержка ' + rtt : ''}`); }
    const dRx = stat.rx - last.rx, dTx = stat.tx - last.tx;
    last = { rx: stat.rx, tx: stat.tx };
    if ((dRx || dTx || stat.q > 1) && ++tick) {
      log(`за 5 с: принято ${Math.round(dRx / 1024)} КБ, отправлено ${Math.round(dTx / 1024)} КБ, в очереди до ${stat.q} пакетов, в буфере до ${Math.round(stat.buf / 1024)} КБ${rtt ? ', задержка ' + rtt : ''}`);
      stat.q = 0; stat.buf = 0;
    }
  }, 5000);
  return iv;
}
// i18n-skip-end

/* ---------------------------------------------------------------- хозяин */
async function p2pHostStart(url) {
  p2pHostStop();
  const st = { url, peers: {}, seen: new Set(), timer: 0, alive: true };
  P2P.host = st;
  const tick = async () => {
    if (!st.alive) return;
    const r = await safe(api.p2p_inbox());
    for (const o of (r && r.offers) || []) {
      if (st.seen.has(o.id)) continue;
      st.seen.add(o.id);
      p2pAnswer(st, o).catch((e) => api.p2p_answer(o.id, null, String(e && e.message || e)).catch(() => {}));
    }
    if (st.alive) st.timer = setTimeout(tick, 4000);   // реже — бережём лимит бесплатного сервера
  };
  tick();
}

async function p2pAnswer(st, offer) {
  const pc = new RTCPeerConnection({ iceServers: await p2pIce() });
  st.peers[offer.id] = pc;
  const stat = { rx: 0, tx: 0, q: 0, buf: 0 };
  p2pMonitor(pc, `друг ${offer.from}`, stat); // i18n-skip: журнал
  p2pIceDiag(pc, `друг ${offer.from}`); // i18n-skip: журнал
  pc.ondatachannel = (e) => {
    const dc = e.channel;
    if (dc.label === 'hello') return;
    p2pPipe(dc, `${st.url}/host/${dc.label}`, stat);
  };
  pc.onconnectionstatechange = () => {
    if (pc.connectionState === 'connected') toast(`${offer.from} подключается к вам напрямую`, 'info', 6000);
    if (['failed', 'closed'].includes(pc.connectionState)) delete st.peers[offer.id];
  };
  await pc.setRemoteDescription(offer.sdp);
  await pc.setLocalDescription(await pc.createAnswer());
  await p2pGathered(pc);
  await api.p2p_answer(offer.id, { type: pc.localDescription.type, sdp: pc.localDescription.sdp }, '');
}

function p2pHostStop() {
  const st = P2P.host;
  if (!st) return;
  st.alive = false;
  clearTimeout(st.timer);
  Object.values(st.peers).forEach((pc) => pc.close());
  P2P.host = null;
}

/* ---------------------------------------------------------------- гость */
/** Соединиться с другом напрямую. Возвращает адрес для игры («127.0.0.1:порт») или бросает ошибку. */
async function p2pJoin(host, onStatus = () => {}, code = '', limitMs = 130000) {
  p2pGuestStop();
  const prep = await api.p2p_guest_prepare();
  // (6.0.34 пробовала «только через ретранслятор» — у друга канал не поднялся совсем, откатили: пусть браузер выберет сам)
  // Прямой путь между домашними провайдерами у вас с другом давал 25–150 КБ за 5 с и скачки задержки до 800 мс (замер 16.11),
  // а через ретранслятор Cloudflare тест на ПК: 2,5 МБ/с. Поэтому гость идёт через него; не вышло — лаунчер сам зайдёт через e4mc.
  const all = await p2pIce();
  const viaRelay = all.length > P2P_ICE.length;
  // 6.0.39: у друга по UDP канал 35 с стоял (задержка 1–1,6 с, потери), потом разогнался; поэтому до ретранслятора идём по TCP/TLS
  // (turn …transport=tcp, turns:). Если таких адресов нет — как раньше.
  let ice = all;
  if (viaRelay) {
    const tcp = all.map(s => ({ ...s, urls: [].concat(s.urls).filter(u => /^turns:|transport=tcp/i.test(u)) })).filter(s => s.urls.length);
    if (tcp.length) ice = tcp;
  }
  const pc = new RTCPeerConnection({ iceServers: ice, iceTransportPolicy: viaRelay ? 'relay' : 'all' });
  const st = { pc, ctl: null, host, stat: { rx: 0, tx: 0, q: 0, buf: 0 } };
  P2P.guest = st;
  p2pMonitor(pc, `подключение к ${host}`, st.stat); // i18n-skip: журнал
  const iceCount = p2pIceDiag(pc, `подключение к ${host}`); // i18n-skip: журнал
  pc.createDataChannel('hello');                       // без канала в приглашении не будет раздела для данных
  await pc.setLocalDescription(await pc.createOffer());
  onStatus('Ищу путь до друга...');
  await p2pGathered(pc);
  safe(api.p2p_log(`подключение к ${host}: ${iceCount()}`)); // i18n-skip: журнал
  const id = p2pId();
  const offer = { type: pc.localDescription.type, sdp: pc.localDescription.sdp };
  const sent = await api.p2p_offer(host, id, offer, code);
  if (sent.error) throw new Error(sent.error);
  onStatus(`Жду ответа от ${host} (обычно секунды, иногда до 2 минут)...`);
  const started = Date.now();
  const deadline = started + limitMs;
  // Сервер v6 приглашения не теряет; один повтор через 15 с — на случай, если хозяин как раз перезапускал лаунчер
  // (хозяин не ответит дважды на одно и то же).
  const resendAt = [15000];
  let answer = null, pollFails = 0;
  while (Date.now() < deadline && P2P.guest === st) {
    await new Promise((r) => setTimeout(r, 2500));
    if (P2P.guest !== st) break;
    onStatus(`Жду ответа от ${host}... ${Math.round((Date.now() - started) / 1000)} из ${Math.round(limitMs / 1000)} сек`);
    const r = await safe(api.p2p_poll(host, id)) || { error: 'Нет связи с сервером VaultMC' };
    // одна «моргнувшая» связь не должна обрывать ожидание, которое длится до двух минут — сдаёмся после нескольких неудач подряд
    if (r.error) { if (++pollFails >= 5) throw new Error(r.error); continue; }
    pollFails = 0;
    if (r.answer) { answer = r.answer; break; }
    if (resendAt.length && Date.now() - started >= resendAt[0]) {
      resendAt.shift();
      await safe(api.p2p_offer(host, id, offer, code));
    }
  }
  if (!answer) throw new Error(`${host} не ответил — возможно, у него закрыт лаунчер или мир не открыт для сети`);
  if (answer.error || !answer.sdp) throw new Error(answer.error || 'Друг не смог принять подключение');
  await pc.setRemoteDescription(answer.sdp);
  onStatus('Соединяюсь напрямую...');
  await new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error('Напрямую соединиться не удалось (строгий NAT у провайдера)')), 30000);
    const check = () => {
      if (pc.connectionState === 'connected') { clearTimeout(t); resolve(); }
      if (pc.connectionState === 'failed') { clearTimeout(t); reject(new Error('Напрямую соединиться не удалось (строгий NAT у провайдера)')); }
    };
    pc.addEventListener('connectionstatechange', check);
    check();
  });
  // мост сообщает о каждом подключении игры — открываем под него отдельный канал
  const ctl = new WebSocket(`${prep.url}/ctl`);
  st.ctl = ctl;
  ctl.onmessage = (e) => {
    let m; try { m = JSON.parse(e.data); } catch (err) { return; }
    if (m.t === 'conn' && pc.connectionState === 'connected') p2pPipe(pc.createDataChannel(m.id), `${prep.url}/guest/${m.id}`, st.stat);
  };
  await new Promise((resolve) => { ctl.onopen = resolve; setTimeout(resolve, 1500); });
  pc.addEventListener('connectionstatechange', () => {
    if (['failed', 'closed'].includes(pc.connectionState) && P2P.guest === st && !st.lost) {
      st.lost = true;
      toast(`Прямое соединение с ${host} прервалось`, 'warn', 8000);
      document.dispatchEvent(new CustomEvent('p2p-lost', { detail: { host } }));   // rooms.js переключит на ретранслятор
    }
  });
  safe(api.p2p_guest_server(host, prep.port));   // мир друга — в «Сетевой игре», чтобы зайти снова после выхода в меню
  return `127.0.0.1:${prep.port}`;
}

function p2pGuestStop() {
  const st = P2P.guest;
  if (!st) return;
  P2P.guest = null;
  try { st.ctl && st.ctl.close(); } catch (e) { /* уже */ }
  try { st.pc.close(); } catch (e) { /* уже */ }
  api.p2p_guest_stop().catch(() => {});
}

onEvent('p2p_host', (r) => { p2pHostStart(r.url); toast('Мир открыт: друзья VaultMC могут зайти к вам напрямую из вкладки «Друзья»', 'info', 8000); });
onEvent('p2p_stop', () => p2pHostStop());
onEvent('state', (s) => { if (s === 'idle') p2pGuestStop(); });
