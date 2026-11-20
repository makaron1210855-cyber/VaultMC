/* «Моды из другой сборки»: докачать в эту сборку моды, которые есть в другой, не создавая новую сборку.
 * Логика — core/modcopy.py: подбор версий под эту сборку (Modrinth), зависимости, проверка конфликтов перед установкой. */

const MC_STATUS = {
  copy: ['', 'тот же файл'],
  download: ['accent', 'версия под эту сборку'],
  no: ['warn', 'не подходит'],
};

function openModCopy(inst, onDone) {
  const others = S.instances.filter((i) => i.id !== inst.id && i.loader !== 'vanilla');
  if (!others.length) return toast('Нет другой сборки с модами', 'warn');
  const m = openModal(`<h3>Моды из другой сборки</h3>
    <p class="muted small">Лаунчер покажет моды, которых нет в «${esc(inst.name)}», и поставит отмеченные. Если версия игры
      или загрузчик другие — скачает версию мода под эту сборку. Мод, который сломал бы сборку, не ставится.</p>
    <label class="field"><span>Откуда взять моды</span><select class="input" id="mcSrc">${others.map((i) =>
      `<option value="${esc(i.id)}">${esc(i.name)} · ${esc(i.loaderLabel)} ${esc(i.game_version)}</option>`).join('')}</select></label>
    <div id="mcBody" style="margin-top:12px"></div>`, { wide: true });
  const body = $('#mcBody', m);
  let plan = null;

  const load = async () => {
    body.innerHTML = '<div class="row muted"><span class="spinner"></span> Сравниваю сборки и ищу версии модов...</div>';
    const src = $('#mcSrc', m).value;
    plan = await safe(api.mod_copy_plan(inst.id, src));
    if (!m.isConnected || $('#mcSrc', m).value !== src) return;
    if (!plan) { body.innerHTML = ''; return; }
    draw();
  };

  const draw = () => {
    const items = plan.items;
    const ok = items.filter((i) => i.status !== 'no');
    const head = `<p class="small muted">${ok.length ? `Можно добавить: ${ok.length}.` : 'Добавить нечего.'}
      ${plan.already ? ` Уже есть в этой сборке: ${plan.already}.` : ''}
      ${plan.same ? '' : ` Версия игры или загрузчик отличаются — моды скачаются под ${esc(inst.loaderLabel)} ${esc(inst.game_version)}.`}</p>`;
    body.innerHTML = head + (items.length ? `
      <div class="row" style="margin:6px 0"><label class="check"><input type="checkbox" id="mcAll" ${ok.length ? '' : 'disabled'}><span class="box">${iconSvg('check')}</span>Выбрать все подходящие</label></div>
      <div class="list" style="max-height:min(380px,46vh);overflow:auto">${items.map((i) => {
        const [cls, label] = MC_STATUS[i.status] || ['', ''];
        return `<div class="item" style="padding:8px 12px${i.status === 'no' ? ';opacity:.55' : ''}">
          <label class="check grow" style="cursor:${i.status === 'no' ? 'default' : 'pointer'};gap:12px">
            <input type="checkbox" data-f="${esc(i.file)}" ${i.status === 'no' ? 'disabled' : ''}><span class="box">${iconSvg('check')}</span>
            <div class="grow"><div class="title">${esc(i.name)}</div>
              <div class="small faint">${esc(i.status === 'download' ? i.newfile : i.file)}${i.reason ? ' · ' + esc(i.reason) : ''}</div></div></label>
          <span class="chip ${cls}">${esc(label)}</span></div>`;
      }).join('')}</div>
      <p class="small faint" style="margin-top:8px">Нужные выбранным модам зависимости из той сборки добавятся сами. Перед установкой
        лаунчер сохранит точку во вкладке «История» — вернуть всё как было можно одной кнопкой.</p>` : '')
      + `<div class="foot"><button class="btn ghost" data-close>Отмена</button>
        <button class="btn primary" id="mcGo" disabled>${ic('download')} Добавить в сборку</button></div>`;
    hydrateIcons(body);
    body.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
    const boxes = () => [...body.querySelectorAll('input[data-f]:not(:disabled)')];
    const sync = () => {
      const n = boxes().filter((b) => b.checked).length;
      $('#mcGo', body).disabled = !n;
      $('#mcGo', body).lastChild.textContent = n ? ` Добавить в сборку (${n})` : ' Добавить в сборку';
    };
    body.addEventListener('change', (e) => {
      if (e.target.id === 'mcAll') boxes().forEach((b) => (b.checked = e.target.checked));
      sync();
    });
    $('#mcGo', body).onclick = () => install(boxes().filter((b) => b.checked).map((b) => b.dataset.f));
  };

  const install = async (files) => {
    const src = $('#mcSrc', m).value;
    $('#mcSrc', m).disabled = true;
    body.innerHTML = `<div class="row muted"><span class="spinner"></span> Ставлю ${files.length} мод(ов) и проверяю, что сборка не сломается...</div>`;
    const r = await safe(api.mod_copy_install(inst.id, src, files));
    if (!m.isConnected) return;
    $('#mcSrc', m).disabled = false;
    if (!r || r.error) { if (r?.error) toast(r.error, 'error'); return draw(); }
    body.innerHTML = `
      <p><b>Добавлено: ${r.installed.length}</b>${r.deps_added.length ? ` (из них зависимости: ${r.deps_added.map(esc).join(', ')})` : ''}</p>
      ${r.installed.length ? `<div class="small muted" style="margin-bottom:10px">${r.installed.map(esc).join(', ')}</div>` : ''}
      ${r.skipped.length ? `<p style="color:var(--warn)">${ic('alert')} Не добавлено, чтобы не сломать сборку: ${r.skipped.length}</p>
        <div class="list">${r.skipped.map((s) => `<div class="item" style="padding:6px 12px"><div class="grow"><div class="title">${esc(s.name)}</div>
          <div class="small faint">${esc(s.reason)}</div></div></div>`).join('')}</div>` : ''}
      <div class="foot"><button class="btn primary" data-close>Готово</button></div>`;
    hydrateIcons(body);
    body.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
    if (onDone) onDone();
  };

  $('#mcSrc', m).onchange = load;
  load();
}
