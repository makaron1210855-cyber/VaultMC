/* «Друзья» — одна вкладка для игры вместе (бывшие «Комнаты» + «Друзья»).
 *
 *   слева  — друзья: добавить по нику, заявки, кто в сети, кнопка «Зайти» у друга с открытым миром;
 *   справа — «Мой мир»: одна кнопка, дальше шаги отмечаются сами (моды → игра → мир открыт).
 *
 * Друзья живут на сервере VaultMC (заявка по нику → друг нажимает «Принять»). Лаунчер раз в минуту спрашивает
 * сервер и показывает всплывающие окошки: «X хочет добавить вас в друзья» и «X открыл мир — Зайти».
 * Моды для игры по сети: e4mc (адрес через интернет) + LAN World Plug-n-Play (онлайн-режим лаунчер выключает сам).
 */

const ROOM_LOADERS = ['fabric', 'forge', 'neoforge', 'quilt'];

/* ================================================================ данные друзей и всплывающие окошки */
const OF = { data: null, error: '', account: '', sig: '', at: 0, seenReq: new Set(), seenOpen: new Set(), busy: false };
const FR = { el: null };   // элемент открытой вкладки «Друзья» (при переходе старая страница ещё секунду живёт рядом)
const HEADS = {};   // ник → {p: обещание скина, at} — чтобы головы не мигали при каждом обновлении списка

// подпись сборки в выпадающем списке: загрузчик и версия одинаково для всех сборок («Fabric 1.20.1», «Forge 1.20.1»)
const optLabel = (i) => `${i.loaderLabel || i.loader || ''} ${i.game_version || ''}`.trim() || i.name || '';
const isOpenWorld = (st) => !!(st && st.state === 'playing' && (st.room || st.p2p || st.relay));

async function paintHeads(root) {
  $$('canvas[data-head]', root).forEach(async (cv) => {
    const n = cv.dataset.head;
    const hit = HEADS[n];
    // скин запоминаем; «нет скина / нет связи» — только на 2 минуты, потом спрашиваем снова
    if (!hit || (hit.empty && Date.now() - hit.at > 120000)) {
      const rec = { at: Date.now(), empty: false };
      rec.p = api.player_skin(n).catch(() => '').then((s) => { rec.empty = !s; return s; });
      HEADS[n] = rec;
    }
    const skin = await HEADS[n].p;
    if (cv.isConnected) drawHead(cv, skin || null, getAccentHex());
  });
}

/** Окошко в углу с кнопками (в отличие от toast не исчезает само). key — чтобы не показывать одно и то же дважды. */
function notice(key, { name = '', title, text = '', actions = [] }) {
  let box = $('#notices');
  if (!box) { box = html('<div class="notices" id="notices"></div>'); document.body.appendChild(box); }
  noticeClose(key);
  const el = html(`<div class="notice" data-key="${esc(key)}">
    ${name ? `<canvas data-head="${esc(name)}"></canvas>` : ''}
    <div class="grow"><b>${title}</b>${text ? `<p>${text}</p>` : ''}
      <div class="acts">${actions.map((a, i) => `<button class="btn sm ${a.primary ? 'primary' : 'ghost'}" data-i="${i}">${a.icon ? ic(a.icon) : ''}${esc(a.label)}</button>`).join('')}</div></div>
    <button class="btn ghost sm icon x" data-x title="Закрыть">${ic('x')}</button></div>`);
  el.onclick = (e) => {
    const b = e.target.closest('[data-i]');
    if (e.target.closest('[data-x]')) return noticeClose(key);
    if (b) { noticeClose(key); actions[+b.dataset.i].run(); }
  };
  box.appendChild(el);
  hydrateIcons(el);
  paintHeads(el);
  return el;
}
function noticeClose(key) {
  $$('#notices .notice').forEach((n) => { if (n.dataset.key === key) n.remove(); });
}

/** Что изменилось с прошлого ответа сервера — показываем окошки. */
function friendsDiff(prev, cur) {
  const st = cur.status || {};
  for (const n of cur.incoming) {
    if (OF.seenReq.has(n)) continue;
    OF.seenReq.add(n);
    notice('req:' + n, { name: n, title: `<span class="no-tr">${esc(n)}</span> хочет добавить вас в друзья`,
      actions: [{ label: 'Принять', primary: true, icon: 'check', run: () => friendAction('accept', n) },
        { label: 'Отклонить', run: () => friendAction('decline', n) }] });
  }
  [...OF.seenReq].forEach((n) => { if (!cur.incoming.includes(n)) { OF.seenReq.delete(n); noticeClose('req:' + n); } });
  for (const n of cur.friends) {
    if (!isOpenWorld(st[n])) { if (OF.seenOpen.delete(n)) noticeClose('open:' + n); continue; }
    if (OF.seenOpen.has(n)) continue;
    OF.seenOpen.add(n);
    if (S.state !== 'idle') continue;                    // сами играем — не отвлекаем
    notice('open:' + n, { name: n, title: `<span class="no-tr">${esc(n)}</span> открыл мир`,
      text: `${st[n].instance ? `«<span class="no-tr">${esc(st[n].instance)}</span>»` : 'Minecraft'}${st[n].version ? ` · ${esc(st[n].version)}` : ''}`,
      actions: [{ label: 'Зайти', primary: true, icon: 'play', run: () => joinFriend(n, ((OF.data || {}).status || {})[n] || st[n]) },
        { label: 'Не сейчас', run: () => {} }] });
  }
  if (prev) cur.friends.forEach((n) => { if (prev.outgoing.includes(n)) toast(`${n} принял вашу заявку — вы друзья`, 'info', 7000); });
}

/** Спросить сервер. quiet — фоновый опрос: ошибки связи не показываем, старый список остаётся на экране. */
async function friendsFetch(quiet = false) {
  const me = S.account ? S.account.username : '';
  if (me !== OF.account) {                               // сменили аккаунт — всё с чистого листа
    Object.assign(OF, { data: null, error: '', account: me, sig: '' });
    OF.seenReq.clear(); OF.seenOpen.clear();
    $$('#notices .notice').forEach((n) => n.remove());
  }
  if (!me || OF.busy) return;
  OF.busy = true;
  let r = null;
  try { r = await api.online_friends('list'); } catch (e) { r = quiet && OF.data ? null : { error: 'Нет связи с сервером VaultMC' }; }
  OF.busy = false;
  if (!r || (S.account ? S.account.username : '') !== me) return;
  OF.at = Date.now();
  if (r.error) { if (quiet && OF.data) return; OF.error = r.error; OF.data = null; }
  else { OF.error = ''; friendsDiff(OF.data, r); OF.data = r; }
  friendsRepaint();
}

/** Верхняя полоса: значок на вкладке «Друзья» (заявки + открытые миры) и головы друзей в сети. */
function friendsChrome() {
  const d = OF.data, st = (d && d.status) || {};
  const badge = $('#friendsBadge');
  const n = d ? d.incoming.length + d.friends.filter((f) => isOpenWorld(st[f])).length : 0;
  if (badge) { badge.textContent = n ? String(n) : ''; badge.classList.toggle('on', !!n); }
  const box = $('#tbOnline');
  if (!box) return;
  const online = d ? sortFriends(d.friends.filter((f) => st[f]), st) : [];
  const sig = online.join(',');
  if (box.__sig === sig) return;
  box.__sig = sig;
  box.classList.toggle('on', online.length > 0);
  box.innerHTML = online.length ? `<span class="heads">${online.slice(0, 3).map((f) => `<canvas data-head="${esc(f)}"></canvas>`).join('')}</span><b>${online.length}</b><span class="txt">в сети</span>` : '';
  box.title = online.length ? `В сети: ${online.join(', ')}` : '';
  paintHeads(box);
}

/** Перерисовать список, только если он действительно изменился (иначе мигал бы каждую минуту). */
function friendsRepaint(force = false) {
  friendsChrome();
  const box = FR.el && FR.el.isConnected ? $('#frList', FR.el) : null;
  if (!box) return;
  const sig = JSON.stringify([OF.error, OF.data && [OF.data.friends, OF.data.incoming, OF.data.outgoing,
    Object.entries(OF.data.status || {}).map(([n, s]) => [n, s.state, s.instance, s.version, s.room, s.p2p])], S.friends, !!S.account]);
  if (!force && sig === OF.sig && box.childElementCount) return;
  OF.sig = sig;
  box.innerHTML = friendsListHtml();
  hydrateIcons(box);
  paintHeads(box);
}

async function friendAction(action, name) {
  const res = await safe(api.online_friends(action, name));
  if (!res) return null;
  if (res.error === 'no_server') { toast('Сервер VaultMC не настроен', 'error'); return null; }
  if (res.error) { toast(res.error, 'error', 9000); return null; }
  OF.data = { ...(OF.data || {}), friends: res.friends, incoming: res.incoming, outgoing: res.outgoing, status: (OF.data || {}).status || {} };
  if (action === 'accept') { toast(`${name} теперь ваш друг`); friendsFetch(true); }
  noticeClose('req:' + name);
  friendsRepaint();
  return res;
}

async function sendFriendRequest(nick) {
  nick = String(nick || '').trim();
  if (!nick) { toast('Введите ник друга', 'warn'); return null; }
  if (S.account && nick.toLowerCase() === S.account.username.toLowerCase()) { toast('Это ваш собственный ник', 'warn'); return null; }
  const res = await friendAction('request', nick);
  if (!res) return null;
  const now = res.friends.some((f) => f.toLowerCase() === nick.toLowerCase());
  toast(now ? `${nick} теперь ваш друг` : `Заявка отправлена. У ${nick} появится окошко — ему останется нажать «Принять»`, 'info', 8000);
  const local = (S.friends || []).find((f) => f.name.toLowerCase() === nick.toLowerCase());
  if (local) { S.friends = await api.friend_remove(local.name); friendsRepaint(); }   // был «по коду» — теперь настоящий друг
  return res;
}

/** Фоновый опрос: раз в минуту, пока лаунчер на виду. Во время игры в свёрнутом лаунчере сервер не дёргаем. */
function friendsPollStart() {
  const tick = () => {
    if (!S.account || document.hidden) return;
    if (S.state !== 'idle' && !document.hasFocus()) return;
    friendsFetch(true);
  };
  setInterval(tick, 60000);
  window.addEventListener('focus', () => { if (Date.now() - OF.at > 20000) tick(); });
  setTimeout(tick, 1500);
}

/* ================================================================ список друзей */
const sortFriends = (list, st) => [...list].sort((a, b) => {
  const w = (n) => (isOpenWorld(st[n]) ? 3 : st[n] ? (st[n].state === 'playing' ? 2 : 1) : 0);
  return w(b) - w(a) || a.localeCompare(b);
});

const frRow = (n, cls, status, actions) => `<div class="fr-row ${cls}" data-n="${esc(n)}">
  <span class="fr-ava"><canvas data-head="${esc(n)}"></canvas><i class="fr-dot"></i></span>
  <div class="grow"><div class="fr-name no-tr">${esc(n)}</div><div class="fr-status">${status}</div></div>${actions}</div>`;

function friendsListHtml() {
  if (!S.account) return `<div class="empty"><b>Войдите в аккаунт</b><span>Друзья привязаны к вашему нику.</span></div>`;
  if (OF.error === 'no_server') return `<div class="empty"><b>Сервер VaultMC не настроен</b><span>В этой сборке лаунчера друзья недоступны.</span></div>`;
  if (OF.error) return `<div class="fr-error">${ic('alert')}<div class="grow"><b>Друзья сейчас недоступны</b><p>${esc(OF.error)}</p></div>
    <button class="btn sm" data-f="retry">${ic('refresh')} Повторить</button></div>`;
  if (!OF.data) return `<div class="fr-loading"><span class="spinner"></span> Загружаю друзей...</div>`;
  const d = OF.data, st = d.status || {};
  const known = new Set([...d.friends, ...d.incoming, ...d.outgoing].map((n) => n.toLowerCase()));
  const local = (S.friends || []).filter((f) => !known.has(f.name.toLowerCase()));
  const x = (title, act = 'remove') => `<button class="btn sm ghost icon fr-x" data-f="${act}" title="${title}">${ic('trash')}</button>`;
  let out = '';
  if (d.incoming.length) {
    out += `<div class="fr-sec">Хотят добавить вас <span class="count">${d.incoming.length}</span></div><div class="fr-rows">`
      + d.incoming.map((n) => frRow(n, 'req', 'хочет добавить вас в друзья',
        `<button class="btn sm primary" data-f="accept">${ic('check')} Принять</button><button class="btn sm ghost" data-f="decline">Отклонить</button>`)).join('') + '</div>';
  }
  const total = d.friends.length;
  const online = d.friends.filter((n) => st[n]).length;
  out += `<div class="fr-sec">Мои друзья <span class="count">${total}</span>${total ? `<span class="fr-online">в сети: ${online}</span>` : ''}<span class="grow"></span>
    <button class="btn sm ghost icon" data-f="retry" title="Обновить список">${ic('refresh')}</button></div>`;
  if (!total && !d.outgoing.length && !local.length && !d.incoming.length) {
    out += `<div class="fr-empty"><b>Пока никого</b>
      <ol><li><i>1</i><span>Введите ник друга в поле выше и нажмите «Добавить»</span></li>
        <li><i>2</i><span>У друга в лаунчере всплывёт окошко — он нажимает «Принять»</span></li>
        <li><i>3</i><span>Кто-то из вас открывает мир, второй нажимает «Зайти»</span></li></ol></div>`;
    return out;
  }
  out += '<div class="fr-rows">' + sortFriends(d.friends, st).map((n) => {
    const s = st[n];
    const where = s && s.state === 'playing' ? `${s.instance ? `«<span class="no-tr">${esc(s.instance)}</span>»` : ''}${s.version ? ` · ${esc(s.version)}` : ''}` : '';
    if (isOpenWorld(s)) return frRow(n, 'open', `<b>открыл мир</b> ${where}`, `<button class="btn primary" data-f="join">${ic('play')} Зайти</button>${x('Убрать из друзей')}`);
    if (s && s.state === 'playing') return frRow(n, 'play', `играет ${where}`, x('Убрать из друзей'));
    if (s) return frRow(n, 'on', 'в сети', x('Убрать из друзей'));
    return frRow(n, 'off', 'не в сети', x('Убрать из друзей'));
  }).join('')
    + d.outgoing.map((n) => frRow(n, 'wait', 'заявка отправлена — ждём, когда друг нажмёт «Принять»',
      `<button class="btn sm ghost" data-f="decline">Отменить</button>`)).join('')
    + local.map((f) => frRow(f.name, 'local', 'добавлен по-старому (по коду) — друг вас не видит',
      `<button class="btn sm" data-f="invite">${ic('plus')} Отправить заявку</button>${x('Убрать', 'rmlocal')}`)).join('') + '</div>';
  return out;
}

/* ================================================================ «Мой мир» (хозяин) */
function roomStage() {
  // 0 — ничего, 1 — ставим моды, 2 — игра запускается, 3 — в игре, мир ещё не открыт, 4 — комната открыта, 5 — обычный LAN
  if (S.lanOpen && S.roomMode) return 4;
  if (S.lanOpen) return 5;                 // открыт обычным «Открыть для сети» (Radmin) — не комната
  if (S.state === 'running') return 3;
  if (['preparing', 'installing'].includes(S.state) && S.roomLaunching) return S.roomPrep ? 1 : 2;
  return 0;
}

function hostPanelHtml() {
  const list = S.instances.filter((i) => ROOM_LOADERS.includes(i.loader));
  const stage = roomStage();
  if (stage === 4) {
    const addr = S.roomAddress, code = S.roomCode;
    return `<div class="fr-host live"><span class="fr-badge"><i class="pulse"></i>Комната открыта</span>
      <h3>Зовите друзей</h3>
      ${code ? `<div class="fr-code"><span class="no-tr">${esc(code)}</span>
          <button class="btn primary" data-h="invite">${ic('copy')} Скопировать приглашение</button></div>
        <p class="fr-small">Отправьте код кому угодно — добавлять в друзья не нужно. Друг вводит его в «Зайти к другу». Друзья из списка просто увидят окошко «Зайти».</p>`
        : `<p>Друзья из вашего списка уже видят окошко «Зайти». ${ic('refresh')} Получаю код для остальных...</p>`}
      ${addr ? `<details class="fr-more"><summary>Адрес для друзей без VaultMC</summary>
          <div class="fr-addr"><span class="mono grow no-tr">${esc(addr)}</span><button class="btn sm" data-h="copy">${ic('copy')}</button></div></details>` : ''}</div>`;
  }
  if (stage === 5) {   // мир открыт для сети обычным способом (Radmin, одна Wi-Fi) — комнату не создавали
    return `<div class="fr-host"><span class="fr-badge wait"><i class="pulse"></i>Мир открыт для сети</span>
      <h3>Играете по локальной сети</h3>
      <p>Мир открыт обычным «Открыть для сети» — как для Radmin или одной Wi-Fi. Лаунчер ничего не создавал и никому его не показывает.</p>
      <button class="btn" data-h="roomnow">${ic('globe')} Сделать комнатой — позвать по коду</button>
      <p class="fr-small">Друзья зайдут напрямую по коду. Адреса e4mc в этот раз не будет — для него откройте мир кнопкой «Открыть мир для друзей».</p></div>`;
  }
  if (stage === 3) {
    return `<div class="fr-host"><span class="fr-badge wait"><i class="pulse"></i>Игра запущена</span>
      <h3>Остался один шаг — в игре</h3>
      <ol class="fr-do"><li>Зайдите в свой мир («Одиночная игра»)</li>
        <li>Нажмите <kbd>Esc</kbd> → <b>«Открыть для сети»</b></li>
        <li>Нажмите <b>«Start LAN World»</b> — больше ничего менять не нужно</li></ol>
      <p class="fr-small">${S.roomLaunching ? 'Как только мир откроется, здесь появится код комнаты, а друзья из списка увидят окошко «Зайти».'
        : 'Это обычный запуск: «Открыть для сети» сработает как всегда (для Radmin). Чтобы позвать друзей по коду, откройте мир, а затем нажмите здесь «Сделать комнатой».'}</p></div>`;
  }
  if (stage === 1 || stage === 2) {
    const li = (n, t) => `<li class="${stage > n ? 'done' : stage === n ? 'now' : ''}"><i>${stage > n ? ic('check') : n}</i><span>${t}</span></li>`;
    return `<div class="fr-host"><span class="fr-badge wait"><i class="pulse"></i>Готовлю</span>
      <h3>Открываю ваш мир</h3>
      <ol class="fr-steps">${li(1, 'Ставлю моды для игры по сети')}${li(2, 'Запускаю игру')}${li(3, 'Вы открываете мир: Esc → «Открыть для сети»')}</ol></div>`;
  }
  if (!list.length) {
    return `<div class="fr-host"><h3>Открыть свой мир</h3>
      <p>Чтобы друзья заходили к вам через интернет, нужна сборка с Fabric, Forge или NeoForge — лаунчер сам добавит в неё два маленьких мода.</p>
      <button class="btn primary" data-h="new">${ic('plus')} Создать сборку</button></div>`;
  }
  const cur = list.find((i) => i.id === (S.roomInstance || S.selected?.id)) || list[0];
  return `<div class="fr-host"><h3>Открыть свой мир</h3>
    <p>Запустите игру отсюда — лаунчер создаст комнату с кодом. Друг зайдёт по коду, даже если он не в друзьях. Без Radmin и настройки роутера.</p>
    <label class="field"><span>Сборка</span><select class="input" id="roomInst">
      ${list.map((i) => `<option value="${i.id}" ${i.id === cur.id ? 'selected' : ''}>${esc(optLabel(i))}</option>`).join('')}</select></label>
    <button class="btn primary" data-h="start" ${S.state !== 'idle' ? 'disabled' : ''}>${ic('globe')} Открыть мир для друзей</button>
    <p class="fr-small">Обычная кнопка «Играть» комнату не создаёт — «Открыть для сети» тогда работает как всегда, например для Radmin.</p>
    <details class="fr-more"><summary>Что должно совпадать у друга</summary>
      <ul class="fr-rules"><li><b>Версия игры</b> — обязательно (у вас ${esc(cur.game_version)}).</li>
        <li><b>Моды с новыми блоками, мобами, предметами</b> — должны быть и у друга.</li>
        <li><b>Моды для FPS, карты, интерфейса</b> — могут быть разными, как и с Radmin.</li></ul>
      <p class="fr-small">Проще всего отправить другу код сборки: сборка → «Экспорт» → «Код для друга».</p></details>
    <details class="fr-more"><summary>Способ подключения</summary>
      <label class="field"><span>Запасной путь, если напрямую не выйдет</span><select class="input" id="roomTunnel">
        ${Object.entries(S.boot.tunnels).map(([k, d]) => `<option value="${k}" ${k === (S.config.tunnel_mod || 'e4mc+lan') ? 'selected' : ''}>${esc(d)}</option>`).join('')}</select></label>
      <p class="fr-small">Сначала друг подключается к вам напрямую, а если провайдер не пускает — через бесплатный сервис e4mc. Мод Krypton с e4mc несовместим, лаунчер выключит его в этой сборке.</p></details></div>`;
}

function hostRepaint() {
  const box = FR.el && FR.el.isConnected ? $('#frHost', FR.el) : null;
  if (!box) return;
  // раскрытые «Дополнительно» и выбранная сборка не сбрасываются, когда панель обновляется по событию игры
  const open = $$('details.fr-more', box).map((d) => d.open);
  const inst = $('#roomInst', box)?.value;
  box.innerHTML = hostPanelHtml();
  $$('details.fr-more', box).forEach((d, i) => { if (open[i]) d.open = true; });
  if (inst && $(`#roomInst option[value="${CSS.escape(inst)}"]`, box)) $('#roomInst', box).value = inst;
  const sel = $('#roomInst', box);
  if (sel) sel.onchange = () => { S.roomInstance = sel.value; };
  const tun = $('#roomTunnel', box);
  if (tun) tun.onchange = () => { S.config.tunnel_mod = tun.value; };   // выбор не теряется до нажатия «Открыть мир»
  hydrateIcons(box);
}

/* ================================================================ вкладка */
registerPage('friends', {
  async render(el) {
    el.innerHTML = `
      <div class="fr-top"><div class="grow"><div class="h1">Друзья и комнаты</div>
        <p class="muted">Откройте свой мир и отправьте другу код — или добавьте друга по нику, чтобы видеть друг друга в сети.</p></div></div>
      <div class="fr-grid">
        <div class="fr-main">
          <form class="fr-add" id="frAdd">${ic('search')}<input id="frNick" placeholder="Ник друга в VaultMC" maxlength="16" autocomplete="off" spellcheck="false">
            <button class="btn primary" type="submit">${ic('plus')} Добавить</button></form>
          <div id="frList"></div>
        </div>
        <aside class="fr-side">
          <div id="frHost"></div>
          <form class="fr-join" id="frJoin"><b>${ic('link')} Зайти к другу</b>
            <span class="fr-small">Друг прислал код комнаты (6 знаков) или адрес — вставьте сюда. Дружить не обязательно.</span>
            <div class="row"><input class="input mono grow" id="joinAddr" placeholder="K7XQ2M" autocomplete="off" spellcheck="false">
              <button class="btn primary" type="submit">${ic('play')} Зайти</button></div></form>
        </aside>
      </div>`;
    hydrateIcons(el);
    FR.el = el;
    OF.sig = '';
    setTimeout(() => this.refresh(el), 0);   // после того как страницу вставят в окно
    S.friends = (await safe(api.friends())) || [];
    friendsFetch();

    $('#frAdd', el).onsubmit = async (e) => {
      e.preventDefault();
      const input = $('#frNick', el), btn = $('button', e.target);
      btn.disabled = true;
      const res = await sendFriendRequest(input.value);
      btn.disabled = false;
      if (res) input.value = '';
    };
    $('#frJoin', el).onsubmit = (e) => {
      e.preventDefault();
      const v = $('#joinAddr', el).value.trim();
      if (!v) return toast('Вставьте код комнаты или адрес, который прислал друг', 'warn');
      joinAny(v);
    };
    el.onclick = async (e) => {
      const f = e.target.closest('[data-f]');
      if (f) {
        const row = f.closest('[data-n]'), n = row ? row.dataset.n : '';
        const a = f.dataset.f;
        if (a === 'retry') { f.disabled = true; OF.sig = ''; await friendsFetch(); return friendsRepaint(true); }
        if (a === 'join') return joinFriend(n, ((OF.data || {}).status || {})[n]);
        if (a === 'rmlocal') { S.friends = await api.friend_remove(n); return friendsRepaint(); }
        if (a === 'remove' && !await confirmDialog('Убрать из друзей?', `${n} пропадёт из списка у вас обоих.`, 'Убрать', true)) return;
        f.disabled = true;
        if (a === 'invite') await sendFriendRequest(n); else await friendAction(a, n);
        if (f.isConnected) f.disabled = false;
        return;
      }
      const h = e.target.closest('[data-h]');
      if (!h) return;
      if (h.dataset.h === 'copy') return copyText(S.roomAddress);
      if (h.dataset.h === 'invite') return copyText(`Заходи ко мне в Minecraft через VaultMC: вкладка «Друзья» → «Зайти к другу» → код ${S.roomCode}`);
      if (h.dataset.h === 'roomnow') {
        h.disabled = true;
        const r = await safe(api.open_room_now());
        if (!r || !okOrToast(r)) { h.disabled = false; return; }
        S.roomMode = true; hostRepaint(); return;
      }
      if (h.dataset.h === 'new') return openNewInstance();
      if (h.dataset.h === 'start') {
        if (!S.account) return showAuth();
        const id = $('#roomInst', el).value;
        const tun = ($('#roomTunnel', el) || {}).value || S.config.tunnel_mod || 'e4mc+lan';
        S.config.tunnel_mod = tun; S.roomInstance = id;
        selectInstance(id);
        h.disabled = true;
        const r = await safe(api.prepare_room(id, tun));
        if (!r || !okOrToast(r)) { h.disabled = false; return; }
        S.roomLaunching = true; S.roomPrep = true; S.pendingRoomLaunch = id;
        hostRepaint();
      }
    };
  },
  refresh(el) { friendsRepaint(true); hostRepaint(); },
});

// старая вкладка «Комнаты» (сохранённая страница, кнопка на главной) ведёт сюда же
registerPage('rooms', { render() { setTimeout(() => go('friends'), 0); } });

// панель «Мой мир» обновляется сама по событиям игры. Перерисовываем после остальных обработчиков события:
// S.state обновляет app.js, а он подключён позже этого файла.
const roomRefresh = () => setTimeout(hostRepaint, 0);
onEvent('state', (s) => {
  if (s === 'running') S.roomPrep = false;
  if (s === 'idle') { S.lanOpen = false; S.roomMode = false; S.roomCode = ''; }   // roomLaunching не сбрасываем: после установки модов «idle» мелькает перед запуском игры
  roomRefresh();
});
onEvent('game_exit', () => { S.roomLaunching = false; S.roomPrep = false; S.lanOpen = false; S.roomMode = false; S.roomCode = ''; roomRefresh(); });
onEvent('room_ready', () => { S.roomPrep = false; roomRefresh(); });
onEvent('lan_open', (p) => { S.lanOpen = true; S.roomMode = !!(p && p.room); roomRefresh(); });
onEvent('room_code', roomRefresh);
onEvent('room_address', roomRefresh);

/* ================================================================ подключение к другу */
/** Что вставили в «Зайти к другу»: 6 знаков — код комнаты, иначе адрес сервера/e4mc. */
async function joinAny(text) {
  const t = text.trim();
  const code = t.replace(/[^A-Za-z0-9]/g, '');
  if (code.length === 6 && !t.includes('.')) return joinByCode(code);
  return joinAddress(t);
}

async function joinByCode(code) {
  const r = await safe(api.room_lookup(code));
  if (!r || !okOrToast(r)) return;
  joinFriend(r.host, { state: 'playing', ...r.info }, r.code);
}

async function joinAddress(address) {
  if (!S.selected) return toast('Сначала создайте сборку', 'warn');
  const check = await safe(api.check_address(address));
  if (check && !check.ok && check.reason === 'dns') {
    // провайдер не находит адрес, а Cloudflare находит — лаунчер сам подключит в обход (core/dnsrescue.py), игроку ничего менять не нужно
    toast('Ваш провайдер не находит этот адрес — лаунчер подключится в обход', 'info', 6000);
  } else if (check && !check.ok) {
    const why = {
      typo: 'Адрес выглядит неполным. Скопируйте его у друга целиком — например <span class="mono">abc-def.e4mc.link</span>.',
      dns: 'Адрес существует, но ваш интернет-провайдер его не находит (проблема DNS) — поэтому игра пишет «Unknown host».<br>'
        + 'Решение: в Windows включите DNS <b>1.1.1.1</b> (Параметры → Сеть → свойства подключения → DNS) или включите любой VPN, затем подключитесь снова.',
      closed: 'Такой адрес сейчас не находится — скорее всего, комната уже закрыта или друг пересоздал её с новым адресом.<br>'
        + 'Попросите друга скопировать адрес заново (он появляется, когда мир открыт для сети). Если друг уверен, что всё работает, — попробуйте DNS 1.1.1.1 или VPN.',
    }[check.reason] || '';
    const go2 = await new Promise((resolve) => {
      const m = openModal(`<h3>${ic('alert')} Не удаётся найти адрес</h3><p class="muted">${why}</p>
        <div class="foot"><button class="btn ghost" data-close>Отмена</button><button class="btn" id="goAnyway">Всё равно подключиться</button></div>`,
      { onClose: () => resolve(false) });
      $('#goAnyway', m).onclick = () => { closeModal(); resolve(true); };
    });
    if (!go2) return;
  }
  address = (check && check.address) || address;
  S.config.server_address = address;
  await play(address);
}

/* Моды хозяина, без которых гостя выкинет из мира (свои звуки, частицы, предметы). Всю сборку друга не ставим:
   предупреждаем и предлагаем только эти моды — с галочками и кнопкой «Зайти без них». true — можно заходить. */
let roomModsWait = null;
const roomModsEnd = (r) => { if (roomModsWait) { const f = roomModsWait; roomModsWait = null; f(r); } };
onEvent('room_mods_done', roomModsEnd);
onEvent('error', () => roomModsEnd(null));

async function roomModsGate(name, st, code) {
  let mods = st.mods;
  const c = code || st.code;
  if (!mods && c) { const r = await api.room_lookup(c).catch(() => null); mods = r && r.info && r.info.mods; }
  const inst = S.selected;
  if (!mods || !mods.length || !inst) return true;
  const chk = await api.room_mods_check(inst.id, mods).catch(() => null);
  if (!chk || !chk.missing || !chk.missing.length) return true;
  const mb = (n) => (n ? `${(n / 1048576).toFixed(1)} МБ` : '');
  const choice = await new Promise((resolve) => {
    const rows = chk.missing.map((m, i) => `<label class="check gm-row"><input type="checkbox" data-i="${i}" ${chk.can ? 'checked' : 'disabled'}>
      <span class="box">${iconSvg('check')}</span><span class="grow no-tr">${esc(m.name)}</span><small class="muted">${mb(m.size)}</small></label>`).join('');
    const m = openModal(`<h3>${ic('alert')} У <span class="no-tr">${esc(name)}</span> есть моды, которых нет у вас</h3>
      <p class="muted">Эти моды добавляют в мир свои звуки, частицы или предметы. Без них игра может выкинуть вас из мира через несколько минут.</p>
      <div class="gm-list">${rows}</div>
      ${chk.can ? `<p class="small muted">Лаунчер поставит в вашу сборку «<span class="no-tr">${esc(inst.name)}</span>» только отмеченные моды (с Modrinth). Остальные моды друга вам не нужны. Убрать их потом можно в модах сборки.</p>`
        : `<p class="small muted">В вашу сборку «<span class="no-tr">${esc(inst.name)}</span>» их поставить нельзя: для этого нужна сборка Fabric той же версии игры.</p>`}
      <div class="foot"><button class="btn ghost" data-close>Отмена</button><button class="btn" id="gmSkip">Зайти без них</button>
        ${chk.can ? `<button class="btn primary" id="gmOk">${ic('download')} Поставить и зайти</button>` : ''}</div>`,
    { onClose: () => resolve(null) });
    $('#gmSkip', m).onclick = () => { closeModal(); resolve([]); };
    if (chk.can) $('#gmOk', m).onclick = () => {
      const picked = $$('.gm-row input:checked', m).map((x) => chk.missing[+x.dataset.i]);
      closeModal(); resolve(picked);
    };
  });
  if (!choice) return false;              // «Отмена» — не заходим
  if (!choice.length) return true;        // отказался от модов — заходим как есть
  const started = await safe(api.room_mods_install(inst.id, choice));
  if (!started || !okOrToast(started)) return false;
  toast('Ставлю моды друга — это меньше минуты', 'info', 6000);
  const done = await new Promise((resolve) => { roomModsWait = resolve; setTimeout(() => roomModsEnd(null), 300000); });
  for (let i = 0; i < 50 && S.state !== 'idle'; i++) await new Promise((r) => setTimeout(r, 100));   // лаунчер освобождается сразу после события
  const res = done && done.result;
  if (!res) { toast('Моды друга поставить не удалось — подробности в «Консоли». Захожу без них', 'warn', 9000); return true; }
  if (res.installed.length) toast(`Моды друга поставлены: ${res.installed.length}`);
  if (res.failed.length) toast(`Не нашлись на Modrinth: ${res.failed.join(', ')}. Попросите эти файлы у друга`, 'warn', 12000);
  return true;
}

/* Зайти к другу в один клик: подбираем сборку той же версии и загрузчика, затем запускаем с адресом мира */
async function joinFriend(name, st, code = '') {
  if (!isOpenWorld(st)) return toast(`${name} уже закрыл мир`, 'warn');
  if (S.state !== 'idle') return toast('Сначала закройте игру, которая сейчас запущена', 'warn');
  // как с Radmin: обязательно совпадает только версия игры. Сначала берём сборку с тем же загрузчиком,
  // иначе любую этой версии (моды для FPS могут отличаться, а с модами-блоками игра сама скажет, чего не хватает)
  const sameVer = S.instances.filter((i) => i.game_version === st.version);
  const same = sameVer.filter((i) => !st.loader || i.loader === st.loader);
  if (st.version && !sameVer.length) {
    return openModal(`<h3>Нужна сборка Minecraft ${esc(st.version)}</h3>
      <p class="muted">${esc(name)} играет на версии <b>${esc(st.version)}</b>${st.loader ? ` (${esc(st.loader)})` : ''}, а у вас нет сборки этой версии.</p>
      <ul class="fr-rules"><li><b>Версия игры</b> должна совпадать — без этого Minecraft не пустит, и с Radmin тоже.</li>
        <li><b>Моды с новыми блоками и предметами</b> — нужны такие же, как у ${esc(name)}.</li>
        <li><b>Моды для FPS и интерфейса</b> — можно свои.</li></ul>
      <p class="small muted">Быстрее всего: попросите у ${esc(name)} код сборки (сборка → «Экспорт» → «Код для друга») и вставьте его в «Сборки» → «По коду».</p>
      <div class="foot"><button class="btn ghost" data-close>Понятно</button><button class="btn primary" id="mkInst">${ic('plus')} Создать сборку ${esc(st.version)}</button></div>`)
      .querySelector('#mkInst').onclick = () => { closeModal(); openNewInstance(); };
  }
  const pick = same.length ? same : sameVer;
  if (pick.length && !pick.some((i) => i.id === S.selected?.id)) selectInstance(pick[0].id);
  if (!same.length && st.loader && pick.length) toast(`У вас нет сборки ${st.loader} ${st.version} — захожу в «${pick[0].name}». Если игра попросит моды, возьмите у друга код сборки`, 'warn', 9000);
  if (!await roomModsGate(name, st, code)) return;
  JOINED.name = name; JOINED.st = st;
  // Порядок: ретранслятор VaultMC (проще и надёжнее всего) → напрямую → e4mc. Каждый следующий — если не вышел предыдущий.
  if (st.relay) {
    const r = await safe(api.relay_guest_start(name, st.relay.port, st.relay.token));
    if (r && r.address) {
      toast(`Захожу к ${name} через ретранслятор VaultMC`);
      netWatch('relay');
      S.config.server_address = r.address;
      return play(r.address);
    }
    if (r && r.reason === 'gone') return toast(`${name} уже закрыл мир`, 'warn');
    if (r && r.error && (st.p2p || st.room)) toast(`Ретранслятор: ${r.error}. Пробую другой способ`, 'warn', 7000);
    else if (r && r.error) return toast(r.error, 'error', 9000);
  }
  if (!st.p2p) return st.room ? viaE4mc(name, st.room) : undefined;
  const m = openModal(`<h3>Подключение к ${esc(name)}</h3>
    <div class="share-wait muted small" id="p2pStatus">${ic('refresh')} Готовлю прямое соединение...</div>
    <p class="note">Если у провайдера строгий NAT и напрямую не выйдет${st.room ? ', лаунчер сам зайдёт через e4mc' : ''}.</p>
    <div class="foot"><button class="btn ghost" data-close>Отмена</button></div>`, { onClose: () => { if (!m.__done) p2pGuestStop(); } });
  const status = (t) => { const el = $('#p2pStatus', m); if (el) el.lastChild.textContent = ' ' + t; };
  try {
    // приглашения сервер передаёт сразу (сервер v6), друг отвечает за 5–10 с — долго ждать незачем
    const address = await p2pJoin(name, status, code, st.room ? 30000 : 45000);
    if (!m.isConnected) return;
    m.__done = true; m.close();
    toast(`Соединились с ${name} напрямую — запускаю игру`);
    netWatch('p2p');
    S.config.server_address = address;
    await play(address);
  } catch (e) {
    if (!m.isConnected) return;
    m.__done = true; m.close(); p2pGuestStop();
    const why = String(e && e.message || e);
    if (st.room) viaE4mc(name, st.room, why);
    else toast(why, 'error', 9000);
  }
}

function viaE4mc(name, address, why = '') {
  toast(`${why ? why + '. ' : ''}Захожу к ${name} через e4mc`, why ? 'warn' : 'info', 9000);
  netWatch('e4mc');
  return joinAddress(address);
}

/* Последний друг, к которому заходили: если прямой канал оборвётся посреди игры, переключаемся на ретранслятор */
const JOINED = { name: '', st: null };
document.addEventListener('p2p-lost', async () => {
  const { name, st } = JOINED;
  if (!st || !st.relay || S.state !== 'running') return;
  const r = await safe(api.relay_guest_start(name, st.relay.port, st.relay.token));
  if (r && r.address) {
    netWatch('relay');
    toast(`Прямая связь с ${name} оборвалась. Зайдите снова: «Сетевая игра» → «VaultMC · мир ${name}» — теперь через ретранслятор`, 'warn', 15000);
  }
});
