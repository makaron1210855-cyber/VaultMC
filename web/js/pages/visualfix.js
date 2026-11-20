/* «Картинка не так?»: найти, какой слой ресурспаков/модов портит вид, и выключить его — по одному, обратимо.
 * Логика — core/visualfix.py (настройки модов в config/) и core/emfmodels.py (отдельные модели). Игра должна быть закрыта. */

function openVisualFix(inst) {
  const m = openModal(`<h3>Картинка выглядит не так?</h3>
    <p class="muted small">Ресурспаки приносят в игру «слои»: свои модели, текстуры, светящиеся детали, соединённые стёкла. Любой
      может нарисовать что-то неправильно (у колокола висит только низ, пятна на мобах, швы на стёклах). Лаунчер не видит
      картинку, поэтому выключайте слои по одному: выключили → запустили игру → посмотрели. Если не помогло — включите обратно.
      Всё записывается, внизу есть «Вернуть как было».</p>
    <div id="vfBody"><div class="row muted"><span class="spinner"></span> Смотрю, что делают ваши паки...</div></div>`, { wide: true });
  const body = $('#vfBody', m);
  let modelQuery = '';

  const act = async (p, ok) => {
    const r = await safe(p);
    if (!m.isConnected) return;
    if (!r || r.error) { if (r?.error) toast(r.error, 'error', 7000); return load(); }
    if (ok) toast(ok, 'info', 4000);
    return load();
  };

  const draw = (d) => {
    const lock = d.running ? `<p class="note">Игра этой сборки запущена — закройте её, тогда изменения можно сохранить.</p>` : '';
    const packs = d.packs.length ? d.packs.map((p) => `
      <div class="item" style="padding:6px 12px;align-items:flex-start"><div class="grow"><div class="title">${esc(p.name)}</div>
        <div class="small muted" style="margin-top:2px">${p.features.map((f) => esc(f.label) + (f.mod ? (f.installed ? '' : ` <span class="chip warn">нет мода ${esc(f.mod)}</span>`) : '')).join(' · ')}</div></div></div>`).join('')
      : '<p class="small faint">Включённые ресурспаки не используют особых слоёв — искать нечего.</p>';
    const layers = d.layers.length ? d.layers.map((l) => `
      <div class="item" style="padding:8px 12px" data-l="${esc(l.id)}">
        <label class="switch" style="padding:0"><input type="checkbox" data-layer ${l.enabled ? 'checked' : ''} ${d.running ? 'disabled' : ''}><span class="track"></span></label>
        <div class="grow"><div class="title">${esc(l.label)} ${l.changed ? '<span class="chip accent">изменено</span>' : ''}</div>
          <div class="small faint" style="margin-top:2px">${esc(l.hint)}</div></div></div>`).join('')
      : '<p class="small faint">В сборке нет модов со слоями, которые можно выключить (Entity Texture Features, Continuity, Enhanced Block Entities).</p>';
    const q = modelQuery.trim().toLowerCase();
    const models = d.models.filter((x) => !q || x.name.includes(q));
    const modelsHtml = !d.emf ? '<p class="small faint">Мода Entity Model Features в сборке нет — пак не может подменять модели.</p>'
      : d.models.length ? `
        <div class="search" style="margin:6px 0">${ic('search')}<input class="input" id="vfSearch" placeholder="Найти модель: bell, chest, bed..." value="${esc(modelQuery)}"></div>
        <div class="list" style="max-height:min(260px,32vh);overflow:auto">${models.map((x) => `
          <div class="item" style="padding:6px 12px" data-mn="${esc(x.name)}">
            <label class="switch" style="padding:0"><input type="checkbox" data-model ${x.disabled ? '' : 'checked'} ${d.running ? 'disabled' : ''}><span class="track"></span></label>
            <div class="grow"><div class="title">${esc(x.name)} ${x.disabled ? '<span class="chip warn">обычная модель</span>' : ''}${x.changed ? ' <span class="chip accent">изменено</span>' : ''}</div>
              <div class="small faint">${x.packs.length ? esc(x.packs.join(', ')) : 'пак выключен'}</div></div></div>`).join('') || '<p class="small faint">Ничего не найдено.</p>'}</div>
        <p class="small faint" style="margin-top:6px">Выключенная модель рисуется обычной, а текстуры пака остаются.</p>`
      : '<p class="small faint">Включённые паки не приносят своих моделей.</p>';
    body.innerHTML = `${lock}
      <div class="h2" style="margin:10px 0 6px">Что делают ваши ресурспаки</div><div class="list">${packs}</div>
      <div class="h2" style="margin:14px 0 6px">Слои, которые можно выключить</div><div class="list">${layers}</div>
      <div class="h2" style="margin:14px 0 4px">Модели из ресурспака</div>${modelsHtml}
      <div class="foot"><button class="btn ghost" id="vfReset" ${d.changed && !d.running ? '' : 'disabled'}>Вернуть как было</button><div class="grow"></div>
        <button class="btn primary" data-close>Готово</button></div>`;
    hydrateIcons(body);
    body.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
    body.querySelectorAll('input[data-layer]').forEach((c) => (c.onchange = () => act(
      api.visual_layer_set(inst.id, c.closest('[data-l]').dataset.l, c.checked), 'Сохранено — запустите игру и посмотрите')));
    body.querySelectorAll('input[data-model]').forEach((c) => (c.onchange = () => {
      const n = c.closest('[data-mn]').dataset.mn;
      act(api.visual_models_set(inst.id, c.checked ? [] : [n], c.checked ? [n] : []), 'Сохранено — запустите игру и посмотрите');
    }));
    const s = $('#vfSearch', body);
    if (s) s.oninput = debounce(() => { modelQuery = s.value; draw(d); const again = $('#vfSearch', body); if (again) { again.focus(); again.setSelectionRange(again.value.length, again.value.length); } }, 150);
    $('#vfReset', body).onclick = async () => {
      if (!await confirmDialog('Вернуть как было?', 'Все слои и модели, которые вы меняли здесь, вернутся к прежнему состоянию.', 'Вернуть')) return;
      act(api.visual_reset(inst.id), 'Всё возвращено');
    };
  };

  async function load() {
    const d = await safe(api.visual_scan(inst.id));
    if (!m.isConnected) return;
    if (!d || d.error) { body.innerHTML = `<p class="note">${esc(d?.error || 'Не удалось проверить сборку')}</p>`; return; }
    draw(d);
  }
  load();
}
