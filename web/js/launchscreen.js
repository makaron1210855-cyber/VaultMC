/* =============================================================================
   Экран запуска: карточка поверх страниц — этапы, живой прогресс и последняя
   строка из консоли. Окно при этом остаётся доступным: карточку можно свернуть,
   переходить по вкладкам и заниматься другим. «Отмена» и «Закрыть игру» — в ней.
   Данные берём из S.state / S.progress (их кладёт app.js), перерисовываем
   карточку только когда меняется этап, остальное — точечно.
   ============================================================================= */

const LAUNCH_STAGES = ['Аккаунт и моды', 'Файлы игры и Java', 'Запуск игры'];
let launchHidden = false;      // игрок свернул карточку (до следующего запуска)
let launchDoneTimer = 0;       // через несколько секунд после старта карточка «Игра запущена» уходит сама
let launchLastLine = '';

function launchCard() {
  let card = document.getElementById('launchCard');
  if (card) return card;
  card = document.createElement('div');
  card.id = 'launchCard';
  card.className = 'launch-card';
  card.hidden = true;
  card.addEventListener('click', (e) => {
    const a = e.target.closest('[data-l]')?.dataset.l;
    if (a === 'hide') { launchHidden = true; card.hidden = true; }
    if (a === 'cancel') api.cancel();
    if (a === 'kill') api.kill_game();
  });
  document.body.appendChild(card);
  return card;
}

/* Номер текущего этапа: 0 — аккаунт и моды, 1 — файлы и Java, 2 — запуск игры, 3 — всё готово */
function launchStageNow() {
  if (S.state === 'running') return LAUNCH_STAGES.length;
  const value = S.progress[0] || 0;
  if (S.state === 'installing') return 1;
  if (value < 0.05) return 0;
  if (value < 0.9) return 1;
  return 2;
}

/* Полная перерисовка — только при смене состояния или этапа (чтобы не мигала карточка) */
function launchBuild(card) {
  const running = S.state === 'running';
  const stage = launchStageNow();
  const inst = S.selected;
  const title = running ? 'Игра запущена' : S.state === 'installing' ? 'Скачиваю игру' : 'Запускаю игру';
  const steps = LAUNCH_STAGES.map((name, i) => {
    const cls = i < stage ? 'done' : i === stage && !running ? 'now' : '';
    return `<li class="${cls}"><span class="ls-dot">${i < stage ? ic('check') : ''}</span>${name}</li>`;
  }).join('');
  card.innerHTML = `
    <div class="lc-head">
      <div class="lc-title"><b>${title}</b><span class="lc-inst">${inst ? esc(inst.name) : ''}</span></div>
      <button class="lc-x" data-l="hide" title="Свернуть">${ic('x')}</button>
    </div>
    <div class="lc-pct"><span class="lc-num">0%</span><span class="lc-text"></span></div>
    <div class="lc-bar"><div></div></div>
    <ol class="lc-steps">${steps}</ol>
    <div class="lc-log" title="Последняя строка из консоли"></div>
    <div class="lc-actions">${running
      ? `<button class="btn danger" data-l="kill">${ic('x')} Закрыть игру</button>`
      : `<button class="btn" data-l="cancel">Отмена</button>`}</div>`;
  card.dataset.key = `${S.state}|${stage}|${inst ? inst.id : ''}`;
  hydrateIcons(card);
  launchUpdate(card);
  if (launchLastLine) card.querySelector('.lc-log').textContent = launchLastLine;
}

/* Лёгкое обновление: полоска, проценты, текст этапа */
function launchUpdate(card) {
  const running = S.state === 'running';
  const value = running ? 1 : (S.progress[0] || 0);
  const text = running ? 'Игра открыта, можно играть' : (S.progress[1] || '');
  const num = card.querySelector('.lc-num');
  if (!num) return;
  num.textContent = `${Math.round(value * 100)}%`;
  card.querySelector('.lc-text').textContent = text;
  card.querySelector('.lc-bar > div').style.width = `${value * 100}%`;
}

function renderLaunch() {
  const card = launchCard();
  const running = S.state === 'running';
  // импорт и создание сборок (S.taskNew) — у них своя строка в нижней панели, карточку не показываем
  if ((!busyState() && !running) || S.taskNew || launchHidden) { card.hidden = true; return; }
  card.hidden = false;
  const key = `${S.state}|${launchStageNow()}|${S.selected ? S.selected.id : ''}`;
  if (card.dataset.key !== key) launchBuild(card);
  else launchUpdate(card);
  clearTimeout(launchDoneTimer);
  if (running) launchDoneTimer = setTimeout(() => { if (S.state === 'running') card.hidden = true; }, 6000);
}

/* События приходят в порядке подписки; app.js обновляет S.state и S.progress после нас — ждём это одним тиком */
let launchPrev = 'idle';
onEvent('state', (st) => {
  if (st === 'preparing' && launchPrev === 'idle') launchHidden = false;   // новый запуск — карточка снова на виду
  if (st === 'idle') launchLastLine = '';
  launchPrev = st;
  setTimeout(renderLaunch, 0);
});
onEvent('progress', () => setTimeout(renderLaunch, 0));
onEvent('log', (lines) => {
  for (const raw of lines) {
    const line = String(raw).split('\n').map((s) => s.trim()).filter(Boolean).pop();
    if (!line || line.startsWith('=====')) continue;
    launchLastLine = line.length > 160 ? line.slice(0, 160) + '…' : line;
  }
  const card = document.getElementById('launchCard');
  const el = card && card.querySelector('.lc-log');
  if (el && !card.hidden) el.textContent = launchLastLine;
});
