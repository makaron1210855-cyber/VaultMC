/* Диагностика: «Почему тормозит», «Какие флаги Java», проверка сети, отчёт для поддержки.
 * Логика — core/lagcheck.py, jvmreport.py, netcheck.py, supportreport.py. Ничего не меняет без нажатия кнопки.
 */


/** Карточка «Диагностика» на вкладке «Оптимизация» сборки. */
function diagCardHtml() {
  return `<div class="card"><div class="card-title">${ic('search')} Диагностика</div>
    <p class="card-sub">Лаунчер не меряет FPS в игре, но читает журналы последней игры и находит причины подвисаний.</p>
    <div class="row wrap"><button class="btn primary" data-d="lag">${ic('zap')} Почему тормозит</button>
      <button class="btn" data-d="jvm">${ic('cpu')} Флаги Java</button>
      <button class="btn" data-d="net">${ic('globe')} Проверить сеть</button>
      <button class="btn" data-d="report">${ic('archive')} Отчёт для поддержки</button></div></div>`;
}

function bindDiag(root, inst) {
  root.addEventListener('click', (e) => {
    const d = e.target.closest('[data-d]')?.dataset.d;
    if (!d) return;
    if (d === 'lag') openLagCheck(inst);
    if (d === 'jvm') openJvmExplain(inst);
    if (d === 'net') openNetCheck();
    if (d === 'report') openSupportReport(inst);
  });
}

/* ---------------------------------------------------------------- почему тормозит */
async function openLagCheck(inst) {
  const m = openModal(`<h3>Почему тормозит — «${esc(inst.name)}»</h3>
    <div class="muted small" id="lgBody"><span class="spinner"></span> Читаю журналы последней игры...</div>`, { wide: true });
  const r = await safe(api.lag_check(inst.id));
  if (!m.isConnected) return;
  const body = $('#lgBody', m);
  if (!r) return m.close();
  if (!r.has_game_log) {
    body.innerHTML = `<p>Журнала игры пока нет — запустите сборку и поиграйте несколько минут, затем нажмите кнопку снова.</p>`;
    return;
  }
  const head = r.verdict === 'ok'
    ? `<div class="crash-item"><div class="grow"><b>${ic('check')} Явных причин тормозов не найдено</b>
        <p class="small muted">В журнале нет ни «не успевал», ни долгих пауз уборки памяти. Если FPS всё равно низкий — это нагрузка на видеокарту: уменьшите прорисовку и выключите шейдеры (вкладка «Оптимизация»).</p></div></div>`
    : '';
  body.innerHTML = `${head}${r.findings.map((f, n) => `
      <div class="crash-item"><div class="grow"><b>${f.level === 'info' ? ic('file') : ic('alert')} ${esc(f.title)}</b><p class="small muted">${esc(f.text)}</p></div>
        ${f.fix ? `<button class="btn sm ${n ? '' : 'primary'}" data-fix="${n}">${esc(f.fix.label)}</button>` : ''}</div>`).join('')}
    <p class="small faint" style="margin-top:10px">${r.has_gc_log ? `Паузы уборки памяти: ${r.gc.count}, самая длинная — ${Math.round(r.gc.max_ms)} мс.` : 'Журнал уборки памяти появится после следующей игры (нужна Java 9+).'}
      ${r.running ? ' Игра сейчас запущена — разбирается то, что уже записано.' : ''}</p>
    <div class="foot"><button class="btn" data-a="report">${ic('archive')} Отчёт для поддержки</button><div class="grow"></div><button class="btn primary" data-close>Закрыть</button></div>`;
  hydrateIcons(body);
  m.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
  m.onclick = async (e) => {
    if (e.target.closest('[data-a=report]')) { m.close(); return openSupportReport(inst); }
    const fixBtn = e.target.closest('[data-fix]');
    if (!fixBtn) return;
    const fix = r.findings[+fixBtn.dataset.fix].fix;
    if (fix.action === 'logs') { m.close(); return go('logs'); }
    if (fix.action === 'optimize') { m.close(); return go('instance', { id: inst.id, tab: 'optimize' }); }
    const res = await safe(api.crash_fix(inst.id, fix.action, fix.arg));
    if (res && res.ok) { toast(res.ok); fixBtn.disabled = true; fixBtn.innerHTML = `${ic('check')} Готово`; hydrateIcons(fixBtn); reloadInstances(); }
    else if (res) toast(res.error, 'error');
  };
}

/* ---------------------------------------------------------------- флаги Java */
async function openJvmExplain(inst) {
  const r = await safe(api.jvm_explain(inst.id));
  if (!r) return;
  openModal(`<h3>Флаги Java — «${esc(inst.name)}»</h3>
    <p class="muted small">Так лаунчер запустит игру. Правила те же, что при запуске; здесь они только объяснены — ничего не меняется.</p>
    <ul class="steps">${r.lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>
    <details class="fr-more"><summary>Полный список флагов</summary><pre class="mono small" style="white-space:pre-wrap;user-select:text">${r.flags.map(esc).join('\n')}</pre></details>
    <p class="small faint">${r.java ? `Нужна Java ${r.java}.` : 'Версия игры ещё не скачана — Java неизвестна.'} Свои флаги: «Настройки → Дополнительные аргументы JVM».</p>
    <div class="foot"><button class="btn primary" data-close>Понятно</button></div>`);
}

/* ---------------------------------------------------------------- проверка сети */
function netRowsHtml(res) {
  const mark = { ok: ['check', 'var(--ok)'], slow: ['alert', 'var(--warn)'], fail: ['x', 'var(--err)'] };
  return res.items.map((i) => `<div class="item" style="padding:8px 12px;align-items:flex-start">
      <span style="color:${mark[i.status][1]};margin-top:2px">${ic(mark[i.status][0])}</span>
      <div class="grow"><div class="title">${esc(i.name)} <span class="small faint">${esc(i.why)}</span></div>
        <div class="small ${i.status === 'ok' ? 'muted' : ''}">${esc(i.detail)}</div>
        ${i.hint ? `<div class="small" style="margin-top:2px">${esc(i.hint)}</div>` : ''}</div></div>`).join('');
}

function openNetCheck() {
  const m = openModal(`<h3>Проверка сети</h3>
    <div class="muted small" id="ncBody"><span class="spinner"></span> Проверяю Mojang, Modrinth, CurseForge, Ely.by, сервер и ретранслятор VaultMC, скорость... (до 15 секунд)</div>`, { wide: true });
  const done = (res) => {
    if (!m.isConnected) return;
    const body = $('#ncBody', m);
    if (res.error) { body.textContent = res.error; return; }
    body.className = '';
    body.innerHTML = `<div class="note ${res.level === 'ok' ? '' : 'warn-note'}" style="margin-bottom:10px">${ic(res.level === 'ok' ? 'check' : 'alert')} ${esc(res.summary)}</div>
      <div class="list">${netRowsHtml(res)}</div>
      <p class="small faint" style="margin-top:8px">Скорость меряется 4 МБ с общедоступной проверки Cloudflare (обычное скачивание, личные данные не отправляются).
        Результат попадёт в отчёт для поддержки.</p>
      <div class="foot"><button class="btn" data-a="again">${ic('refresh')} Проверить ещё раз</button><div class="grow"></div><button class="btn primary" data-close>Закрыть</button></div>`;
    hydrateIcons(body);
    m.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
    $('[data-a=again]', m).onclick = () => { m.close(); openNetCheck(); };
  };
  netWaiter = done;
  api.net_check_start().catch(() => {});
}
// результат проверки приходит событием (onEvent не умеет отписываться — поэтому один обработчик на всё время)
let netWaiter = null;
onEvent('netcheck_done', (res) => { const f = netWaiter; netWaiter = null; if (f) f(res); });

/* ---------------------------------------------------------------- отчёт для поддержки */
async function openSupportReport(inst) {
  inst = inst || S.selected;
  if (!inst) return toast('Сначала создайте сборку', 'warn');
  const m = openModal(`<h3>Отчёт для поддержки</h3><div class="muted small" id="srBody"><span class="spinner"></span> Собираю и убираю личное...</div>`, { wide: true });
  let hide = true;
  const draw = async () => {
    const r = await safe(api.support_report_preview(inst.id, hide));
    if (!m.isConnected) return;
    if (!r) return m.close();
    const removed = Object.entries(r.removed).map(([k, n]) => `${esc(k)}: ${n}`).join(', ') || 'нечего было вырезать';
    $('#srBody', m).className = '';
    $('#srBody', m).innerHTML = `<p class="muted small">Один архив для того, кому вы отправите его сами: лаунчер никуда его не отправляет. Личное уже вырезано.</p>
      <div class="list">${r.files.map((f) => `<div class="item" style="padding:6px 12px"><div class="grow mono small">${esc(f.name)}</div><span class="small faint">${fmtSize(f.size)}</span></div>`).join('')}</div>
      <p class="small" style="margin-top:8px"><b>Вырезано:</b> ${removed} (имя Windows в путях, почта, IP-адреса, токены, ключи).
        Файлы аккаунтов и ключей в отчёт не входят никогда.</p>
      <label class="switch" style="margin:6px 0"><input type="checkbox" id="srNick" ${hide ? 'checked' : ''}><span class="track"></span><span class="lbl">Скрыть мой ник</span></label>
      ${r.net ? '' : `<p class="small faint">Проверка сети в отчёт не вошла — нажмите «Проверить сеть», если проблема с загрузкой или друзьями.</p>`}
      <div class="foot"><button class="btn ghost" data-close>Отмена</button><button class="btn primary" id="srSave">${ic('download')} Сохранить отчёт...</button></div>`;
    hydrateIcons($('#srBody', m));
    m.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
    $('#srNick', m).onchange = (e) => { hide = e.target.checked; draw(); };
    $('#srSave', m).onclick = async () => {
      const res = await safe(api.support_report_save(inst.id, hide));
      if (!res || !res.path && !res.error) return;
      if (res.error) return toast(res.error, 'error');
      m.close();
      toast(`Отчёт сохранён: ${res.path}`, 'info', 9000);
    };
  };
  draw();
}
