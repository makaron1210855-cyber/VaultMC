/* Консоль: лог лаунчера и игры. Строки добавляются пачками — без лагов даже при «болтливых» модах. */

const LOG_LIMIT = 3000;
let logLines = [];

function logClass(line) {
  if (/\b(ERROR|FATAL|Exception|\[Ошибка\])/.test(line)) return 'err';
  if (/\bWARN/.test(line)) return 'warn';
  if (/^(=====|\[[А-ЯЁ])/.test(line)) return 'sys';  // строки самого лаунчера
  return '';
}

function appendLogs(lines) {
  logLines.push(...lines);
  if (logLines.length > LOG_LIMIT) logLines = logLines.slice(-LOG_LIMIT);
  // последний — это открытая сейчас страница (уходящая ещё ~0,2 с висит в документе)
  const box = [...document.querySelectorAll('#logBox')].pop();
  if (!box) return;
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  const frag = document.createDocumentFragment();
  for (const l of lines) { const d = document.createElement('div'); d.className = logClass(l); d.textContent = l; frag.appendChild(d); }
  box.appendChild(frag);
  while (box.childElementCount > LOG_LIMIT) box.firstElementChild.remove();
  if (stick) box.scrollTop = box.scrollHeight;
}

registerPage('logs', {
  render(el) {
    el.innerHTML = `<div class="row" style="margin-bottom:12px"><div class="h1 grow" style="margin:0">Консоль</div>
      <button class="btn ghost sm" data-a="copy">${ic('copy')} Копировать</button>
      <button class="btn ghost sm" data-a="crash">${ic('file')} crash-reports</button>
      <button class="btn ghost sm" data-a="logs">${ic('folder')} logs</button>
      <button class="btn ghost sm" data-a="clear">${ic('trash')} Очистить</button></div>
      <div class="log" id="logBox"></div>`;
    const box = $('#logBox', el);
    const frag = document.createDocumentFragment();
    for (const l of logLines) { const d = document.createElement('div'); d.className = logClass(l); d.textContent = l; frag.appendChild(d); }
    box.appendChild(frag);
    requestAnimationFrame(() => (box.scrollTop = box.scrollHeight));
    el.onclick = (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'copy') copyText(logLines.join('\n'));
      if (a === 'clear') { logLines = []; box.innerHTML = ''; api.clear_logs(); }
      if (a === 'crash' && S.selected) api.open_folder('crash-reports', S.selected.id);
      if (a === 'logs' && S.selected) api.open_folder('logs', S.selected.id);
    };
  },
});
