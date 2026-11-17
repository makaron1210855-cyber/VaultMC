/* =============================================================================
   Запуск интерфейса: загрузка состояния из Python, нижняя панель, заголовок окна,
   обработка событий (прогресс, игра, установка) и кнопка «Играть».
   ============================================================================= */

/* ---------------------------------------------------------------- кнопка «Играть» */
async function play(server, force = false) {
  if (!S.account) return showAuth();
  const inst = S.selected;
  if (!inst) { toast('Сначала создайте сборку', 'warn'); return openNewInstance(); }
  const address = typeof server === 'string' ? server : '';
  const res = await safe(api.play(inst.id, address, force));
  if (res && res.modcheck) return showModCheck(res, address);
  if (res && okOrToast(res)) toast(address ? `Запускаю «${inst.name}» и подключаюсь к ${address}` : `Запускаю «${inst.name}»...`);
}

/* Проверка модов до запуска: что точно уронит игру и кнопка исправить прямо здесь */
function showModCheck(res, address) {
  const items = res.modcheck;
  const m = openModal(`
    <div class="crash-head">${ic('alert')}<div><h3>Игра с такими модами не запустится</h3>
      <p class="muted small">Лаунчер проверил моды сборки до запуска. Исправьте — и можно играть.</p></div></div>
    <div class="crash-list">${items.map((f, n) => `
      <div class="crash-item"><div class="grow"><b>${esc(f.title)}</b><p class="small muted">${esc(f.text)}</p></div>
        ${f.fix ? `<button class="btn sm ${n ? '' : 'primary'}" data-fix="${n}">${esc(f.fix.label)}</button>` : ''}</div>`).join('')}</div>
    <div class="foot"><button class="btn ghost" data-a="force" title="Если уверены, что всё в порядке">Запустить всё равно</button>
      <div class="grow"></div><button class="btn" data-close>Отмена</button>
      <button class="btn primary" data-a="again">${ic('play')} Проверить и запустить</button></div>`, { wide: true });
  m.onclick = async (e) => {
    const fixBtn = e.target.closest('[data-fix]');
    if (fixBtn) {
      const fix = items[+fixBtn.dataset.fix].fix;
      fixBtn.disabled = true;
      const r = await safe(api.crash_fix(res.instance, fix.action, fix.arg));
      if (r && r.ok) { toast(r.ok); fixBtn.innerHTML = `${ic('check')} Готово`; hydrateIcons(fixBtn); reloadInstances(); return; }
      fixBtn.disabled = false;
      if (r && r.find) {   // зависимость не нашлась автоматически — открываем каталог с поиском
        toast(r.error, 'warn', 8000); m.close(); return go('catalog', { instance: res.instance, query: r.find });
      }
      if (r) toast(r.error, 'error');
      return;
    }
    const a = e.target.closest('[data-a]')?.dataset.a;
    if (a === 'again') { m.close(); play(address); }
    if (a === 'force') { m.close(); play(address, true); }
  };
}

const busyState = () => S.state === 'installing' || S.state === 'preparing';

/* ---------------------------------------------------------------- нижняя панель (все страницы, кроме лобби) */
function renderPlaybar() {
  const bar = $('#playbar');
  if (S.page === 'lobby') { bar.classList.add('hidden-bar'); return; }
  bar.classList.remove('hidden-bar');
  const inst = S.selected;
  const [value, text] = S.progress;
  // задача создаёт НОВУЮ сборку (импорт, каталог, код): в панели не показываем выбранную (иначе кажется, что файлы пишутся в неё)
  const importing = busyState() && !!S.taskNew;
  const shown = importing ? null : inst;
  const rgb = shown ? hexToRgb(shown.color).join(',') : '124,77,255';
  bar.style.setProperty('--pb-rgb', rgb);
  let action;
  if (S.state === 'running') action = `<button class="btn danger play" data-x="kill">${ic('x')} Закрыть игру</button>`;
  else if (S.state === 'installing' || S.state === 'preparing') action = `<button class="btn" data-x="cancel">Отмена</button><button class="btn play" disabled><span class="spinner"></span> ${Math.round(value * 100)}%</button>`;
  else action = `<button class="btn primary play" data-x="play">${ic('play')} ИГРАТЬ</button>`;
  bar.innerHTML = `
    ${shown ? '<canvas></canvas>' : ''}
    <div class="pb-name">${shown ? `<b>${esc(shown.name)}</b><div class="pb-chips"><span class="chip sand">${esc(shown.loaderLabel)}</span><span class="chip">${esc(shown.game_version)}</span></div>`
      : importing ? '<b>Импорт сборки</b><div class="small muted">Появится в списке после окончания</div>'
      : '<b>Нет сборки</b><div class="small muted">Создайте первую сборку</div>'}</div>
    <div class="pb-status"><small><span>${esc(text)}</span><span>${value > 0 && value < 1 ? Math.round(value * 100) + '%' : ''}</span></small>
      <div class="progress ${busyState() ? '' : 'idle'}"><div style="width:${value * 100}%"></div></div></div>
    ${shown ? `<div class="pb-actions"><button class="btn" data-x="mods" title="Моды">${ic('puzzle')}<span>Моды</span></button>
      <button class="btn" data-x="logs" title="Логи">${ic('terminal')}<span>Логи</span></button>
      <button class="btn" data-x="folder" title="Папка">${ic('folder')}<span>Папка</span></button>
      <button class="btn" data-x="settings" title="Настройки сборки">${ic('gear')}<span>Настройки</span></button></div>` : `<button class="btn" data-x="import">${ic('download')} Импорт</button><button class="btn" data-x="new">${ic('plus')} Новая сборка</button>`}
    ${action}`;
  if (shown) drawInstanceIcon($('canvas', bar), shown.name, shown.color, shown.icon);
  hydrateIcons(bar);
  bar.onclick = (e) => {
    if (e.target.closest('canvas, .pb-name') && S.instances.length) return openInstancePicker();   // сменить сборку
    const x = e.target.closest('[data-x]')?.dataset.x;
    if (x === 'play') play();
    if (x === 'kill') api.kill_game();
    if (x === 'import') safe(api.import_instance()).then((r) => { if (r && okOrToast(r) && r.started) toast('Импортирую сборку — прогресс внизу'); });
    if (x === 'cancel') api.cancel();
    if (x === 'mods') go('instance', { id: inst.id, tab: 'mods' });
    if (x === 'logs') go('logs');
    if (x === 'folder') api.open_folder('game', inst.id);
    if (x === 'settings') go('instance', { id: inst.id, tab: 'settings' });
    if (x === 'new') openNewInstance();
  };
}

/** Лёгкое обновление прогресса без перерисовки панели (частые события). */
function updateProgress() {
  const [value, text] = S.progress;
  const bar = $('#playbar');
  const fill = $('.progress > div', bar);
  if (fill && !bar.classList.contains('hidden-bar')) {
    fill.style.width = value * 100 + '%';
    fill.parentElement.classList.toggle('idle', !busyState());   // полоска видна только во время загрузки
    const small = $('.pb-status small', bar);
    if (small) small.innerHTML = `<span>${esc(text)}</span><span>${value > 0 && value < 1 ? Math.round(value * 100) + '%' : ''}</span>`;
    const pb = $('.btn.play[disabled]', bar);
    if (pb) pb.innerHTML = `<span class="spinner"></span> ${Math.round(value * 100)}%`;
  }
  if (S.page === 'lobby') renderBigPlay([...document.querySelectorAll('#bigPlay')].pop());
}

function renderTitleInstance() {
  const b = $('#tbInstance'), inst = S.selected;
  const ver = inst && inst.game_version ? `<i style="font-style:normal;font-size:11px;opacity:.7;margin-left:6px">${esc(inst.game_version)}${inst.loader ? ' · ' + esc(inst.loader) : ''}</i>` : '';
  b.innerHTML = inst ? `<canvas></canvas><b>${esc(inst.name)}</b>${ver}<span class="mini" data-t="play" title="Играть">${ic('play')}</span><span class="mini" data-t="cfg" title="Настройки сборки">${ic('gear')}</span>`
    : `<b class="muted" style="text-align:center">Нет сборок — создать</b>`;
  if (inst) drawInstanceIcon($('canvas', b), inst.name, inst.color, inst.icon);
  hydrateIcons(b);
}

/* ---------------------------------------------------------------- события из Python */
onEvent('log', (lines) => appendLogs(lines));
onEvent('progress', ([v, t]) => { S.progress = [v, t]; updateProgress(); });
onEvent('task_new', (v) => { S.taskNew = !!v; });
// во время игры лаунчер не рисует анимации — видеокарта целиком отдаётся игре
function setGamingMode(state) { document.body.classList.toggle('gaming', state === 'running' || state === 'preparing'); }
onEvent('state', (state) => {
  S.state = state;
  setGamingMode(state);
  renderPlaybar();
  if (S.page === 'lobby') renderBigPlay([...document.querySelectorAll('#bigPlay')].pop());
  if (state === 'idle') reloadInstances().then(() => { if (['instances', 'lobby', 'instance'].includes(S.page)) refreshPage(); });
});
onEvent('error', (msg) => toast(msg, 'error', 7000));
onEvent('win_state', (st) => {   // кнопка □ ⇄ «восстановить»
  const b = $('[data-win=maximize]'); if (!b) return;
  const max = st === 'maximized';
  document.body.classList.toggle('win-max', max);
  b.title = max ? 'Восстановить размер' : 'Развернуть';
  b.innerHTML = `<i data-icon="${max ? 'restore' : 'square'}"></i>`; hydrateIcons(b);
});
onEvent('installed', (v) => toast(`Игра ${v} скачана — можно играть`));
onEvent('simulated', () => toast('Имитация запуска завершена (режим имитации в настройках)'));
onEvent('versions_loaded', (r) => {
  if (!r.online) toast('Нет связи с серверами — список версий из кэша', 'warn');
  if (window.__versionsModal && document.body.contains(window.__versionsModal)) window.__versionsModal.__reload();
});
onEvent('game_exit', (info) => {
  if (info.code !== 0 && !info.killed) showCrash(info);
  else if (info.code === 0) toast(`Сессия: ${Math.round(info.seconds / 60)} мин. До встречи!`);
  S.roomAddress = '';                      // игра закрыта — мир больше не открыт, адрес не показываем
  safe(api.bootstrap()).then((b) => { if (b) { S.account = b.account; S.accounts = b.accounts; } });
});
onEvent('conflicts_fixed', (list) => {
  // лаунчер сам выключил мод, который ломает игру вместе с другим, — говорим, что и почему
  list.forEach((m) => toast(`${m}. Включить обратно — в модах сборки.`, 'warn', 12000));
});
/* Простой: 30 с без мыши и клавиатуры или окно не в фокусе (например, вы в игре) — останавливаем декоративные
   анимации (персонаж, блик на «Играть», 3D-скин). Лаунчер тогда почти не тратит процессор и видеокарту. */
let uiIdleTimer = 0;
function uiWake() {
  clearTimeout(uiIdleTimer);
  uiIdleTimer = setTimeout(() => document.body.classList.add('ui-idle'), 30000);
  if (!document.body.classList.contains('ui-idle')) return;
  document.body.classList.remove('ui-idle');
  document.querySelectorAll('.rig').forEach((r) => rigLookAt(r.parentElement, null));
  document.querySelectorAll('.lh-host').forEach((w) => w.__lh && w.__lh.wake());
  document.querySelectorAll('.s3-wrap').forEach((w) => w.__s3wake && w.__s3wake());
}
['mousemove', 'mousedown', 'keydown', 'wheel', 'focus'].forEach((ev) => window.addEventListener(ev, uiWake, { passive: true }));
window.addEventListener('blur', () => { clearTimeout(uiIdleTimer); document.body.classList.add('ui-idle'); });
uiWake();
onEvent('room_problem', (text) => toast(text, 'warn', 15000));
onEvent('room_address', (addr) => {
  S.roomAddress = addr;
});
onEvent('room_code', (code) => {
  S.roomCode = code;
  toast(`Комната открыта. Код ${code} — отправьте его другу, а друзья из списка увидят окошко «Зайти»`, 'info', 10000);
});
onEvent('nick_problem', (text) => toast(text, 'warn', 16000));
onEvent('optimization_done', (r) => {
  const [done, skipped, reason] = r.result;
  const names = { sodium: 'рендеринг', lithium: 'физика', 'ferrite-core': 'FerriteCore', immediatelyfast: 'ImmediatelyFast', entityculling: 'EntityCulling', modernfix: 'ModernFix' };
  if (!skipped.length) return toast(`Оптимизация: установлено файлов — ${done.length}`, 'info', 6000);
  // пропущено — объясняем нормальным окном, а не мелким уведомлением
  openModal(`<h3>${done.length ? 'Набор оптимизации поставлен частично' : 'Набор оптимизации не поставлен'}</h3>
    ${done.length ? `<p class="muted">Установлено файлов: ${done.length}.</p>` : ''}
    <p>Не установлено: <b>${skipped.map((s) => esc(names[s] || s)).join(', ')}</b>.</p>
    <p class="muted">Почему: ${esc(reason || 'нет версии под эту игру')}.</p>
    <div class="foot"><button class="btn primary" data-close>Понятно</button></div>`);
  if (S.page === 'instance') refreshPage();
});
// лаунчер распознал моды сборки — обновляем отметки «установлено» на открытой странице
onEvent('mods_updated', (r) => {
  const [done, errors] = r.result;
  if (errors.length) toast(`Обновлено ${done}, не удалось ${errors.length}: ${errors[0]}`, 'warn', 9000);
  else toast(`Моды обновлены: ${done}`);
  if (S.page === 'instance') refreshPage();
});
// ресурспаки и шейдеры обновлены — перерисовываем открытую сборку
onEvent('packs_updated', (r) => {
  if (r.errors && r.errors.length) toast(`Обновлено ${r.done}, не удалось ${r.errors.length}: ${r.errors[0]}`, 'warn', 9000);
  else toast(`Ресурспаки и шейдеры обновлены: ${r.done}`);
  if (S.page === 'instance') refreshPage();
});
onEvent('open_pack', () => openPackFile());
// после долгой игры с подвисаниями — подсказка (core/lagcheck.py разобрал журналы)
onEvent('lag_hint', (r) => {
  const inst = S.instances.find((i) => i.id === r.instance);
  if (!inst) return;
  notice('lag:' + inst.id, { title: 'Игра подвисала', text: esc(r.text),
    actions: [{ label: 'Почему тормозит', primary: true, icon: 'zap', run: () => openLagCheck(inst) }] });
});
// перед запуском: мало свободной памяти, 32-битная или неподходящая Java (core/launch.py _check_resources)
onEvent('launch_warning', (r) => {
  if (!r || !r.text) return;
  notice('launchwarn:' + r.kind, { title: 'Перед запуском', text: esc(r.text) });
});
// файлы лаунчера повреждены (core/integrity.py) — один раз за запуск предлагаем починить
setTimeout(async () => {
  const h = await api.launcher_health().catch(() => null);
  if (!h || !h.damagedCount) return;
  if (await confirmDialog('Файлы лаунчера повреждены',
    `Не хватает или изменено файлов: ${h.damagedCount} (например, ${h.damaged[0]}). Часто их удаляет антивирус. Лаунчер может работать с ошибками. Починить? Скачается установщик, лаунчер переустановится и откроется сам — сборки, миры и аккаунты не пострадают.`,
    'Починить')) {
    const r = await safe(api.repair_launcher());
    if (r && okOrToast(r)) toast('Скачиваю установщик...', 'info', 8000);
  }
}, 12000);
// сборка из проводника (двойной щелчок по .mrpack): лаунчер сам запускает импорт
async function openPackFile() {
  const r = await safe(api.take_open_pack());
  if (r && r.page) go(r.page);                         // нажали на окошко Windows о друге (vaultmc://friends)
  else if (r && r.code) openImportByCode(r.code);      // ссылка на сборку с сайта (vaultmc://pack/КОД)
  else if (r && r.started) toast('Импортирую сборку — прогресс внизу');
  else if (r && r.error) toast(r.error, 'error');
}
onEvent('mods_synced', (instanceId) => {
  // мягкое обновление: не перерисовываем страницу целиком (иначе сбивается ввод в поиске и нажатия)
  if (!currentPageEl || $('#modalRoot')?.classList.contains('open')) return;
  if (S.page === 'catalog' && currentPageEl.__st && currentPageEl.__st.instance === instanceId) loadCatalog(currentPageEl);
  if (S.page === 'instance' && currentPageEl.__id === instanceId && ['mods', 'optimize'].includes(currentPageEl.__tab)) {
    const inst = S.instances.find((i) => i.id === instanceId);
    if (inst) renderTab(currentPageEl, inst);
  }
});
onEvent('room_ready', (r) => {
  const [, skipped] = r.result;
  const tunnelMod = r.tunnel === 'e4all' ? 'e4all' : 'e4mc';
  if (skipped.includes(tunnelMod)) { S.roomLaunching = false; S.pendingRoomLaunch = null; }
  if (skipped.includes(tunnelMod)) return toast(`Не удалось поставить ${tunnelMod} для этой версии игры. Попробуйте другой мод-туннель или другую версию.`, 'error', 9000);
  if (skipped.includes('mcwifipnp')) {
    toast('LAN World Plug-n-Play нет для этой версии игры — без него к вам зайдут только игроки с лицензией. Для игры без лицензии выберите в «Дополнительно» вариант e4all.', 'warn', 12000);
  }
  if (S.pendingRoomLaunch) { selectInstance(S.pendingRoomLaunch); S.pendingRoomLaunch = null; play(); }
});
onEvent('modpack_installed', async (inst) => {
  await reloadInstances(); selectInstance(inst.id);
  toast(`Сборка «${inst.name}» готова. Игра скачается при первом запуске.`, 'info', 8000);
  (inst.problems || []).slice(0, 3).forEach((p) => toast(p, 'warn', 10000));
  go('instance', { id: inst.id });
});

/* ---------------------------------------------------------------- старт */
async function boot() {
  await bridgeReady();
  hydrateIcons();
  const b = await api.bootstrap();
  S.boot = b;
  S.config = b.config; S.account = b.account; S.accounts = b.accounts; S.instances = b.instances;
  S.state = b.state; S.roomAddress = b.roomAddress; S.lanOpen = !!b.lanOpen;
  S.roomMode = !!b.roomMode; S.roomCode = b.roomCode || '';
  // страница перезагрузилась, пока мир открыт (например, смена языка) — снова принимаем прямые подключения друзей
  if (b.p2pHost) p2pHostStart(b.p2pHost);
  setGamingMode(b.state);
  S.friends = (await safe(api.friends())) || [];
  friendsPollStart();   // заявки в друзья и «друг открыл мир» всплывают окошками на любой вкладке
  openPackFile();       // сборку открыли двойным щелчком (.mrpack) — импортируем её
  applyAccent(S.config.accent);
  applyTheme(S.config.theme); applyScale(S.config.ui_scale);
  setLanguage((S.config.ui || {}).lang);
  S.lobbyBg = (await safe(api.lobby_bg())) || '';
  $('#appVersion').textContent = 'v' + b.version;
  logLines = b.logs || [];
  if (!b.inApp) $('#winButtons').style.display = 'none';
  if (b.inApp) { addResizeEdges(); addWindowDrag(); }

  $('#nav').onclick = $('.nav-bottom').onclick = (e) => {
    const btn = e.target.closest('button'); if (!btn) return;
    if (btn.dataset.page && btn.dataset.page !== S.page) go(btn.dataset.page);
  };
  $('.titlebar').ondblclick = (e) => {   // двойной щелчок по заголовку — развернуть/восстановить
    if (e.target.closest('button, input, a, [data-t], #accountChip, #tbInstance')) return;
    api.win_maximize();
  };
  $('#winButtons').onclick = (e) => {
    const w = e.target.closest('[data-win]')?.dataset.win;
    if (w === 'minimize') api.win_minimize();
    if (w === 'maximize') api.win_maximize();
    if (w === 'close') api.win_close();
  };
  $('#accountChip').onclick = (e) => openAccountMenu(e.currentTarget);
  $('#tbOnline').onclick = () => go('friends');
  $('#profileCard').onclick = () => openSkinModal();
  $('#tbInstance').onclick = (e) => {
    const t = e.target.closest('[data-t]')?.dataset.t;
    if (t === 'play') return play();
    if (t === 'cfg' && S.selected) return go('instance', { id: S.selected.id });
    if (S.instances.length) openInstancePicker(); else openNewInstance();
  };
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { closeDropdown(); if ($('#modalRoot').classList.contains('open')) closeModal(); }
    const ctrl = e.ctrlKey || e.metaKey, typing = e.target.matches?.('input, textarea, select');
    // масштаб: Ctrl + «+» / «−» / 0
    if (ctrl && !e.altKey && ['=', '+', '-', '_', '0'].includes(e.key)) {
      e.preventDefault();
      const cur = +S.config.ui_scale || 100;
      setScale(e.key === '0' ? 100 : cur + (e.key === '-' || e.key === '_' ? -5 : 5));
      return;
    }
    // Alt+1…5 — вкладки по порядку; Ctrl+Enter — «Играть»
    if (e.altKey && !ctrl && /^[1-9]$/.test(e.key) && !$('#modalRoot').classList.contains('open')) {
      const tab = $$('#nav [data-page], #nav button')[+e.key - 1];
      if (tab) { e.preventDefault(); tab.click(); }
    }
    if (ctrl && e.key === 'Enter' && !typing && !$('#modalRoot').classList.contains('open')) { e.preventDefault(); play(); }
    // F5 / Ctrl+R — обновляем только текущую страницу, а не весь лаунчер (иначе WebView перезагрузится целиком)
    if (e.key === 'F5' || ((e.ctrlKey || e.metaKey) && (e.key === 'r' || e.key === 'R' || e.key === 'к' || e.key === 'К'))) {
      e.preventDefault();
      if (!$('#modalRoot').classList.contains('open')) reloadPage();
    }
  });
  $('#tbReload').onclick = () => reloadPage();

  renderTitleInstance();
  renderAccount();
  const start = S.config.ui?.page || 'lobby';
  go(PAGES[start] ? start : 'lobby', { noSave: true });
  if (!S.account) showAuth();
  else if (!(S.config.ui || {}).tutorialDone) maybeShowTutorial();
  else if (b.firstRun && b.inApp) offerShortcut();
}

async function offerShortcut() {
  if (await confirmDialog('Ярлык на рабочем столе', 'Создать ярлык VaultMC с иконкой на рабочем столе? Тогда лаунчер будет открываться двойным кликом.', 'Создать')) {
    const r = await safe(api.create_shortcut());
    if (r && okOrToast(r)) toast('Ярлык создан');
  } else api.dismiss_shortcut();
}

boot().catch((e) => { console.error(e); document.body.insertAdjacentHTML('beforeend', `<div class="toast error" style="position:fixed;left:20px;bottom:20px">Ошибка запуска интерфейса: ${esc(e.message || e)}</div>`); });

/* ================================================================= окно «Игра вылетела» */
function showCrash(info) {
  const items = info.analysis || [];
  const m = openModal(`
    <div class="crash-head">${ic('alert')}<div><h3>Игра вылетела</h3>
      <p class="muted small">Код ${esc(String(info.code))} · через ${Math.max(1, Math.round(info.seconds / 60))} мин. Вот что удалось понять:</p></div></div>
    <div class="crash-list">${items.map((f, n) => `
      <div class="crash-item"><div class="grow"><b>${esc(f.title)}</b><p class="small muted">${esc(f.text)}</p></div>
        ${f.fix ? `<button class="btn sm ${n ? '' : 'primary'}" data-fix="${n}">${esc(f.fix.label)}</button>` : ''}</div>`).join('')
      || '<div class="crash-item"><div class="grow"><b>Причину не удалось определить</b><p class="small muted">Посмотрите последние строки в консоли.</p></div></div>'}</div>
    ${S.config.crash_stats_asked ? '' : `<div class="crash-item" id="statAsk"><div class="grow"><b>Присылать анонимную статистику падений?</b><p class="small muted">Только тип проблемы и версии лаунчера и игры — без ника, путей и названий модов. Это помогает находить частые вылеты. Изменить можно в «Настройках».</p></div><button class="btn sm primary" data-stat="1">Присылать</button><button class="btn sm" data-stat="0">Не надо</button></div>`}
    <div class="foot">${info.crash ? `<button class="btn ghost" data-a="report">${ic('file')} Отчёт о вылете</button>` : ''}
      <button class="btn ghost" data-a="logs">${ic('terminal')} Консоль</button>
      <button class="btn ghost" data-a="support">${ic('archive')} Отчёт для поддержки</button><div class="grow"></div>
      <button class="btn" data-close>Закрыть</button><button class="btn primary" data-a="play">${ic('play')} Запустить снова</button></div>`, { wide: true });
  m.onclick = async (e) => {
    const statBtn = e.target.closest('[data-stat]');
    if (statBtn) {
      const yes = statBtn.dataset.stat === '1';
      await safe(api.crash_stats_choice(yes)); S.config.crash_stats = yes; S.config.crash_stats_asked = true;
      $('#statAsk', m)?.remove(); toast(yes ? 'Спасибо! Статистика включена' : 'Хорошо, ничего не отправляем', 'info', 2500);
      return;
    }
    const fixBtn = e.target.closest('[data-fix]');
    if (fixBtn) {
      const fix = items[+fixBtn.dataset.fix].fix;
      if (fix.action === 'logs') { m.close(); return go('logs'); }
      if (fix.action === 'find_mod') { m.close(); return go('catalog', { instance: info.instance, query: fix.arg }); }
      if (fix.action === 'open_worlds') { m.close(); return go('instance', { id: info.instance || S.selected?.id, tab: 'worlds' }); }
      const r = await safe(api.crash_fix(info.instance || '', fix.action, fix.arg));
      if (r && r.ok) {
        toast(r.ok); fixBtn.disabled = true; fixBtn.innerHTML = `${ic('check')} Готово`; hydrateIcons(fixBtn);
        if (fix.action === 'java_auto') S.config.java_auto = true;
        if (fix.action === 'clear_jvm') S.config.extra_jvm_args = '';
        reloadInstances();
      } else if (r) toast(r.error, 'error');
      return;
    }
    const a = e.target.closest('[data-a]')?.dataset.a;
    if (a === 'logs') { m.close(); go('logs'); }
    if (a === 'report') api.open_folder('crash-reports', info.instance || S.selected?.id || '');
    if (a === 'support') { m.close(); openSupportReport(S.instances.find((i) => i.id === info.instance) || S.selected); }
    if (a === 'play') { m.close(); if (info.instance) selectInstance(info.instance); play(); }
  };
}

/* Края окна: у окна своя верхняя полоса вместо системной рамки, поэтому
   тянуть за край учим сами — невидимые полоски по краям, курсор-стрелка. */
function addResizeEdges() {
  if ($('.rz')) return;
  for (const edge of ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']) {
    const d = document.createElement('div');
    d.className = `rz rz-${edge}`;
    d.onmousedown = (e) => {
      if (e.button !== 0 || document.body.classList.contains('win-max')) return;
      e.preventDefault();
      api.win_start_resize(edge);
    };
    document.body.appendChild(d);
  }
}

/* Перетаскивание окна за верхнюю полосу — так же, как изменение размера:
   пока кнопка мыши зажата, окно двигает сама Windows (без задержек JS). */
const NO_DRAG = 'button, input, select, textarea, a, [data-t], #tbInstance, #accountChip, .win-buttons';
function addWindowDrag() {
  $('.titlebar').addEventListener('mousedown', (e) => {
    if (e.button !== 0 || e.detail > 1 || e.target.closest(NO_DRAG)) return;
    if (document.body.classList.contains('win-max')) return;   // развёрнутое окно не двигаем — двойной щелчок вернёт размер
    e.preventDefault();
    api.win_start_resize('move');
  });
}
