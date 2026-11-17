/* Вкладка сборки «История»: точки сборки (core/snapshots.py) — что менялось и кнопка «Вернуть сборку сюда».
 * В точку входят моды, настройки модов, ресурспаки, шейдеры и options.txt. Миры — во вкладке «Миры».
 */

function histChangesHtml(c) {
  if (!c) return '';
  const parts = [];
  if (c.added.length) parts.push(`<span class="chip accent">+${c.added.length} мод.</span>`);
  if (c.removed.length) parts.push(`<span class="chip pink">−${c.removed.length} мод.</span>`);
  if (c.updated.length) parts.push(`<span class="chip">обновлено: ${c.updated.length}</span>`);
  if (c.toggled.length) parts.push(`<span class="chip">вкл/выкл: ${c.toggled.length}</span>`);
  if (c.settings) parts.push(`<span class="chip sand">настройки: ${c.settings}</span>`);
  return parts.join('');
}

function histDetailsHtml(c) {
  const li = (t) => `<li>${t}</li>`;
  const rows = [
    ...c.added.map((n) => li(`<b>Добавлен</b> ${esc(n)}`)),
    ...c.removed.map((n) => li(`<b>Удалён</b> ${esc(n)}`)),
    ...c.updated.map((u) => li(`<b>Обновлён</b> ${esc(u.from)} → ${esc(u.to)}`)),
    ...c.toggled.map((t) => li(`<b>${t.on ? 'Включён' : 'Выключен'}</b> ${esc(t.name)}`)),
    ...c.settings_files.map((f) => li(`<b>Изменён</b> ${esc(f)}`)),
  ];
  if (c.settings > c.settings_files.length) rows.push(li(`и ещё файлов настроек: ${c.settings - c.settings_files.length}`));
  return rows.length ? `<ul class="hist-list">${rows.join('')}</ul>` : '';
}

const histEmpty = (c) => !c || (!c.added.length && !c.removed.length && !c.updated.length && !c.toggled.length && !c.settings);

async function renderHistory(body, inst) {
  body.innerHTML = '<div class="row muted"><span class="spinner"></span> Загрузка...</div>';
  const res = await safe(api.snapshots(inst.id));
  if (!res || !body.isConnected) return;
  const items = res.items || [];
  const pending = res.pending && !histEmpty(res.pending) ? res.pending : null;
  body.innerHTML = `
    <div class="row" style="margin-bottom:10px"><div class="small muted grow">Точка запоминает моды, их настройки, ресурспаки, шейдеры и настройки игры. Миры в точку не входят — их копии во вкладке «Миры».
      Лаунчер сам сохраняет точку перед обновлением модов, откатом и «Починкой».</div>
      <button class="btn primary" data-a="save">${ic('archive')} Сохранить точку</button></div>
    ${pending ? `<div class="note" style="margin-bottom:10px">${ic('alert')} С последней точки сборка изменилась: ${histChangesHtml(pending)}
      <details class="fr-more"><summary>Что именно</summary>${histDetailsHtml(pending)}</details></div>` : ''}
    <div class="list">${items.map((s) => `
      <div class="item hist-item" data-id="${esc(s.id)}">
        <div class="grow"><div class="title">${esc(s.reason || 'Точка')}</div>
          <div class="small faint">${esc(fmtWhen(s.at))} · ${s.mods} мод. · ${esc(fmtSize(s.size))}</div>
          ${s.first ? '<div class="small faint">Первая точка этой сборки</div>'
            : histEmpty(s.changes) ? '' : `<div class="row wrap" style="gap:6px;margin-top:6px">${histChangesHtml(s.changes)}</div>
              <details class="fr-more"><summary>Что изменилось</summary>${histDetailsHtml(s.changes)}</details>`}</div>
        <button class="btn sm" data-a="restore" ${res.running ? 'disabled title="Закройте игру"' : ''}>${ic('refresh')} Вернуть сборку сюда</button>
        <button class="btn sm danger" data-a="del" title="Удалить точку">${ic('trash')}</button></div>`).join('')
      || `<div class="empty"><b>Точек пока нет</b><span>Нажмите «Сохранить точку» перед экспериментами с модами — если что-то сломается, сборку можно будет вернуть одной кнопкой.</span></div>`}</div>`;
  hydrateIcons(body);
  body.onclick = async (e) => {
    const a = e.target.closest('[data-a]')?.dataset.a;
    if (!a) return;
    const id = e.target.closest('[data-id]')?.dataset.id;
    if (a === 'save') return histSaveDialog(inst);
    if (a === 'restore' && await confirmDialog('Вернуть сборку к этой точке?',
      'Моды, их настройки, ресурспаки и шейдеры станут такими, как в этой точке. Миры не изменятся. Текущее состояние лаунчер сначала сохранит отдельной точкой — передумаете, вернётесь к ней здесь же.',
      'Вернуть')) {
      okOrToast(await safe(api.snapshot_restore(inst.id, id)));
    }
    if (a === 'del' && await confirmDialog('Удалить точку?', 'Вернуться к ней будет нельзя.', 'Удалить', true)) {
      if (okOrToast(await safe(api.snapshot_delete(inst.id, id)))) renderHistory(body, inst);
    }
  };
}

function histSaveDialog(inst) {
  const m = openModal(`<h3>Сохранить точку сборки</h3>
    <p class="muted small">Подпишите, чтобы потом было понятно, что это за точка. Например: «До установки шейдеров».</p>
    <label class="field"><span>Подпись (необязательно)</span><input class="input" id="histNote" maxlength="120"></label>
    <div class="foot"><button class="btn ghost" data-close>Отмена</button><button class="btn primary" id="histGo">${ic('archive')} Сохранить</button></div>`);
  const input = $('#histNote', m);
  input.focus();
  const go1 = async () => { if (okOrToast(await safe(api.snapshot_create(inst.id, input.value)))) m.close(); };
  $('#histGo', m).onclick = go1;
  input.onkeydown = (e) => { if (e.key === 'Enter') go1(); };
}

const histRefresh = () => { if (S.page === 'instance' && currentPageEl && currentPageEl.__tab === 'history') refreshPage(); };
onEvent('snapshot_done', (r) => {
  toast(r && r.same ? 'С прошлой точки ничего не изменилось — новая не нужна' : 'Точка сборки сохранена');
  histRefresh();
});
onEvent('snapshot_restored', () => {
  toast('Сборка возвращена к точке. Прежнее состояние сохранено отдельной точкой');
  if (S.page === 'instance') refreshPage();
});
