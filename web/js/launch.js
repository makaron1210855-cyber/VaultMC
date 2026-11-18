/* =============================================================================
   Окно «Подождите…» на время запуска (web/launch.html). Python присылает
   снимок состояния через window.__launch(данные): этап, прогресс, новые строки
   лога. Кнопка «Отмена» вызывает Python: pywebview.api.cancel().
   ============================================================================= */

const LAUNCH_STEPS = ['Аккаунт и моды', 'Файлы игры и Java', 'Запуск игры'];
const LOG_MAX = 400;
let launchLang = 'ru';
const ltr = (s) => (launchLang === 'en' && typeof trText === 'function' ? trText(s) : s);

/* Этап: 0 — аккаунт и моды, 1 — файлы и Java, 2 — запуск игры */
function launchStepNow(state, value) {
  if (state === 'installing') return 1;
  if (value < 0.05) return 0;
  if (value < 0.9) return 1;
  return 2;
}

function renderSteps(stage, running) {
  const box = document.getElementById('steps');
  box.innerHTML = LAUNCH_STEPS.map((name, i) => {
    const cls = running || i < stage ? 'done' : i === stage ? 'now' : '';
    return `<li class="${cls}">${ltr(name)}</li>`;
  }).join('');
}

window.__launch = (d) => {
  launchLang = d.lang === 'en' ? 'en' : 'ru';
  document.documentElement.lang = launchLang;
  const running = d.state === 'running';
  const value = running ? 1 : Math.max(0, Math.min(1, d.value || 0));
  document.title = ltr('Подождите…') + ' — VaultMC';
  document.getElementById('sub').textContent = ltr(running ? 'Игра запущена' : d.state === 'installing' ? 'Скачиваю игру' : 'Запускаю игру');
  document.getElementById('inst').textContent = d.title || '';
  document.getElementById('pct').textContent = Math.round(value * 100) + '%';
  document.getElementById('text').textContent = ltr(d.text || '');
  document.getElementById('bar').style.width = value * 100 + '%';
  document.getElementById('cancel').textContent = ltr(running ? 'Закрыть игру' : 'Отмена');
  renderSteps(launchStepNow(d.state, value), running);
  if (d.lines && d.lines.length) {
    const log = document.getElementById('log');
    const stick = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
    for (const line of d.lines) {
      const row = document.createElement('div');
      row.textContent = line;
      log.appendChild(row);
    }
    while (log.childElementCount > LOG_MAX) log.removeChild(log.firstChild);
    if (stick) log.scrollTop = log.scrollHeight;
  }
};

document.getElementById('cancel').onclick = () => {
  if (window.pywebview && window.pywebview.api) window.pywebview.api.cancel();
};
