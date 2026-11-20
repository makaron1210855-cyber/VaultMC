/* Сборки: сетка карточек, страница сборки (моды / оптимизация / настройки), создание новой сборки. */

function instCardHtml(inst, selected) {
  return `<div class="inst-card ${selected ? 'selected' : ''}" style="--c:${inst.color}" data-id="${inst.id}">
    <canvas></canvas>
    <div class="grow"><b>${esc(inst.name)}</b>
      <div class="meta"><span>${esc(inst.game_version)} · ${esc(inst.loaderLabel.toLowerCase())}</span></div>
      <div class="meta"><span title="Наиграно в этой сборке">${ic('clock')} ${esc(inst.playTime)}</span><span>${ic('puzzle')} ${inst.modsCount} мод.</span>
        ${inst.installed ? '' : '<span class="chip warn">не скачана</span>'}</div></div>
    <div class="quick"><button class="btn sm icon primary" data-q="play" title="Играть">${ic('play')}</button>
      <button class="btn sm icon" data-q="export" title="Поделиться / экспорт">${ic('link')}</button>
      <button class="btn sm icon" data-q="open" title="Настроить">${ic('sliders')}</button></div>
  </div>`;
}

function bindInstCards(root) {
  $$('.inst-card[data-id]', root).forEach((card) => {
    const inst = S.instances.find((i) => i.id === card.dataset.id);
    drawInstanceIcon($('canvas', card), inst.name, inst.color, inst.icon);
    card.onclick = (e) => {
      const q = e.target.closest('[data-q]')?.dataset.q;
      selectInstance(inst.id);
      if (q === 'play') play();
      else if (q === 'open') go('instance', { id: inst.id });
      else if (q === 'export') openExportMenu(e.target.closest('[data-q]'), inst);
      else { $$('.inst-card', root).forEach((c) => c.classList.toggle('selected', c === card)); }
    };
    card.ondblclick = () => go('instance', { id: inst.id });
  });
}

function selectInstance(id) {
  S.config.selected_instance = id;
  api.select_instance(id);
  renderTitleInstance();
  renderPlaybar();
}

registerPage('instances', {
  render(el) { this.refresh(el); },
  refresh(el) {
    const recent = S.instances;
    el.innerHTML = `
      <div class="row"><div class="h1 grow">Сборки</div>
        <button class="btn ghost" data-a="import">${ic('download')} Импорт сборки</button>
        <button class="btn ghost" data-a="bycode">${ic('link')} По коду</button>
        <button class="btn ghost" data-a="modpack">${ic('archive')} Готовые сборки</button>
        <button class="btn primary" data-a="new">${ic('plus')} Новая сборка</button></div>
      <div class="inst-grid">
        ${recent.map((i) => instCardHtml(i, i.id === S.selected?.id)).join('')}
        <button class="inst-card add" data-a="new">${ic('plus')}<span>Новая сборка</span></button>
        ${recent.length < 3 ? '<div class="inst-card placeholder"></div>'.repeat(3 - recent.length) : ''}
      </div>
      <p class="note">Каждая сборка — отдельная папка игры: свои моды, конфиги, миры и ресурспаки. Общие файлы игры не дублируются.
      Двойной клик по карточке — настройки сборки.</p>`;
    hydrateIcons(el);
    bindInstCards(el);
    el.onclick = async (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'new') openNewInstance();
      if (a === 'modpack') go('packs');
      if (a === 'bycode') openImportByCode();
      if (a === 'import') {
        const r = await safe(api.import_instance());
        if (r && okOrToast(r) && r.started) toast('Импортирую сборку — прогресс внизу');
      }
    };
  },
});

/* ================================================================= страница сборки */
registerPage('instance', {
  render(el, { id, tab = 'mods' }) { el.__id = id; el.__tab = tab; this.refresh(el); },
  async refresh(el) {
    const inst = S.instances.find((i) => i.id === el.__id);
    // сборку удалили (или страница открылась без сборки) — возвращаемся к списку, там есть «Импорт» и «Новая сборка»
    if (!inst) { setTimeout(() => go('instances'), 0); return; }
    el.innerHTML = `
      <div class="inst-head" style="--c:${inst.color}">
        <button class="inst-pic" data-a="pic" title="Сменить картинку сборки"><canvas></canvas><span>${ic('image')}</span></button>
        <div class="grow"><h2 class="inst-name">${esc(inst.name)}<button class="btn sm ghost icon" data-a="rename" title="Переименовать">${ic('pencil')}</button></h2>
          <div class="row" style="margin-top:6px"><span class="chip sand">${esc(inst.loaderLabel)}</span><span class="chip">${esc(inst.game_version)}</span>
          <span class="chip" title="Наиграно в этой сборке">${ic('clock')} ${esc(inst.playTime)}</span>${inst.installed ? '' : '<span class="chip warn">не скачана</span>'}</div>
          ${inst.notes ? `<div class="small muted" style="margin-top:6px">${esc(inst.notes)}</div>` : ''}</div>
        <div class="row wrap" style="justify-content:flex-end;max-width:420px">
          <button class="btn primary" data-a="play">${ic('play')} Играть</button>
          <button class="btn" data-a="folder">${ic('folder')} Папка</button>
          <button class="btn" data-a="export">${ic('upload')} Экспорт</button>
          <button class="btn" data-a="backup">${ic('archive')} Бэкап миров</button>
          <button class="btn" data-a="copy">${ic('copy')} Копия</button>
          <button class="btn danger" data-a="delete">${ic('trash')}</button>
        </div>
      </div>
      ${segHtml('tab', [['mods', 'Моды'], ['packs', 'Ресурспаки и шейдеры'], ['worlds', 'Миры'], ['history', 'История'], ['servers', 'Серверы'], ['dedicated', 'Свой сервер'], ['shots', 'Скриншоты'], ['optimize', 'Оптимизация'], ['settings', 'Настройки']], el.__tab)}
      <div class="spacer"></div><div id="tabBody"></div>`;
    S.config.selected_instance = inst.id;
    drawInstanceIcon($('.inst-head canvas', el), inst.name, inst.color, inst.icon);
    bindSegs(el, (_, v) => { el.__tab = v; renderTab(el, inst); api.save_ui('instanceTab', v); });
    $('.inst-head', el).onclick = async (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (!a) return;
      if (a === 'play') { selectInstance(inst.id); play(); }
      if (a === 'pic') instancePictureMenu(e.target.closest('[data-a]'), inst);
      if (a === 'rename') renameInstance(inst);
      if (a === 'folder') api.open_folder('game', inst.id);
      if (a === 'export') openExportMenu(e.target.closest('[data-a]'), inst);
      if (a === 'backup') {
        const r = await safe(api.backup_worlds(inst.id));
        if (r && r.error) toast(r.error, 'warn', 7000);
        else if (r) toast(r.path ? 'Бэкап миров: ' + r.path : 'В сборке пока нет миров', r.path ? 'info' : 'warn');
      }
      if (a === 'copy') { const c = await safe(api.duplicate_instance(inst.id)); if (c) { await reloadInstances(); toast('Создана копия сборки'); go('instance', { id: c.id }); } }
      if (a === 'delete' && await confirmDialog('Удалить сборку?', `«${inst.name}» будет удалена вместе с мирами и модами.`, 'Удалить', true)) {
        if ((await safe(api.delete_instance(inst.id).then(() => true))) !== true) return; await reloadInstances(); toast('Сборка удалена'); go('instances');
      }
    };
    renderTab(el, inst);
  },
});

async function renderTab(el, inst) {
  const body = $('#tabBody', el);
  if (el.__tab === 'worlds') return renderWorlds(body, inst);
  if (el.__tab === 'servers') return renderServers(body, inst);
  if (el.__tab === 'dedicated') return renderDedicated(body, inst);
  if (el.__tab === 'shots') return renderShots(body, inst);
  if (el.__tab === 'history') return renderHistory(body, inst);
  if (el.__tab === 'mods') {
    body.innerHTML = `<div class="row wrap" style="margin-bottom:10px">
        <div class="search grow" style="min-width:220px">${ic('search')}<input class="input" id="modFilter" placeholder="Фильтр модов"></div>
        <button class="btn" data-a="add">${ic('plus')} Файл .jar</button>
        ${inst.loader !== 'vanilla' ? `<button class="btn" data-a="updates">${ic('refresh')} Обновления</button>
        <button class="btn" data-a="rollback" title="Вернуть мод к версии до обновления">${ic('refresh')} Откат</button>` : ''}
        <button class="btn" data-a="repair" title="Только если Minecraft не запускается или даёт чёрный экран. Если игра работает — не нужно">${ic('shield')} Починка</button>
        ${inst.loader !== 'vanilla' ? `<button class="btn" data-a="modcopy" title="Добавить моды, которые есть в другой вашей сборке">${ic('copy')} Из другой сборки</button>
        <button class="btn" data-a="audit" title="Найти моды, которые точно бесполезны или мешают (два рендера, лишние библиотеки и т. п.)">${ic('eraser')} Лишние моды</button>` : ''}
        <button class="btn primary" data-a="catalog">${ic('download')} Найти моды</button></div>
      <div class="list" id="modList"><div class="row muted"><span class="spinner"></span> Загрузка...</div></div>`;
    hydrateIcons(body);
    let mods = await safe(api.instance_mods(inst.id)) || [];
    const draw = () => {
      const q = $('#modFilter', body).value.toLowerCase();
      const shown = mods.filter((m) => m.name.toLowerCase().includes(q));
      $('#modList', body).innerHTML = shown.length ? shown.map((m) => `
        <div class="item ${m.enabled ? '' : 'off'}" data-f="${esc(m.file)}">
          <label class="switch" style="padding:0"><input type="checkbox" data-t ${m.enabled ? 'checked' : ''}><span class="track"></span></label>
          <div class="grow"><div class="title">${esc(m.name)}</div><div class="small faint">${esc(m.file)} · ${m.size} КБ</div></div>
          <button class="btn sm danger" data-d>${ic('trash')}</button></div>`).join('')
        : `<div class="empty"><b>${inst.supports_mods === false || inst.loader === 'vanilla' ? 'Vanilla-сборка' : 'Модов пока нет'}</b>
           <span>${inst.loader === 'vanilla' ? 'Моды работают в сборках с Fabric, Forge или NeoForge. Создайте такую сборку.' : 'Нажмите «Найти моды» — установка в один клик.'}</span></div>`;
      hydrateIcons(body);
    };
    draw();
    $('#modFilter', body).oninput = draw;
    body.onclick = async (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'add') { mods = await safe(api.add_mod_files(inst.id)) || mods; draw(); }
      if (a === 'catalog') go('catalog', { instance: inst.id });
      if (a === 'updates') openModUpdates(inst, e.target.closest('[data-a]'));
      if (a === 'repair') openRepair(inst, async () => { mods = await safe(api.instance_mods(inst.id)) || mods; draw(); });
      if (a === 'rollback') openModRollback(inst, async () => { mods = await safe(api.instance_mods(inst.id)) || mods; draw(); });
      if (a === 'audit') openModAudit(inst, async () => { mods = await safe(api.instance_mods(inst.id)) || mods; if (body.isConnected) draw(); });
      if (a === 'modcopy') openModCopy(inst, async () => { mods = await safe(api.instance_mods(inst.id)) || mods; if (body.isConnected) draw(); });
      const row = e.target.closest('.item[data-f]');
      if (row && e.target.closest('[data-d]')) {
        if (await confirmDialog('Удалить мод?', row.dataset.f, 'Удалить', true)) { mods = await safe(api.delete_mod(inst.id, row.dataset.f)) || mods; draw(); }
      }
    };
    body.onchange = async (e) => {
      const row = e.target.closest('.item[data-f]');
      if (row && e.target.matches('[data-t]')) { mods = await safe(api.toggle_mod(inst.id, row.dataset.f)) || mods; draw(); }
    };
  }

  if (el.__tab === 'optimize') {
    const forgeLike = inst.loader === 'forge' || inst.loader === 'neoforge';
    const pack = forgeLike ? S.boot.optimizationForge : S.boot.optimization;
    body.innerHTML = `
      ${diagCardHtml()}
      <div class="card at-card" id="atCard"><div class="row"><div class="grow"><div class="card-title">${ic('cpu')} Автонастройка под ваш ПК</div>
        <p class="card-sub" style="margin:0">Лаунчер посмотрит память, процессор и видеокарту и подберёт память для игры, графику и моды оптимизации. FPS внутри игры он не замеряет — подбор по характеристикам компьютера.</p></div>
        <button class="btn primary" data-a="atscan">${ic('search')} Определить ПК</button></div>
        <div id="atResult"></div></div>
      <div class="two">
        <div class="card"><div class="card-title">${ic('zap')} Моды оптимизации</div>
          <p class="card-sub">Проверенный набор${forgeLike ? ` для ${esc(inst.loaderLabel)}` : ' для Fabric'}: больше FPS, меньше расход памяти, быстрее загрузка. Ставится в эту сборку одной кнопкой${forgeLike ? ' — лаунчер сам подберёт версии модов под Forge/NeoForge' : ''}.</p>
          <div class="list" id="optList">${Object.entries(pack).map(([slug, d]) => `<div class="item" style="padding:8px 10px" data-slug="${slug}">${checkHtml(slug, '', true)}<div class="small grow">${esc(d)}</div></div>`).join('')}</div>
          <div class="spacer"></div>
          <button class="btn primary" data-a="pack" ${inst.loader === 'vanilla' ? 'disabled' : ''}>${ic('download')} Установить выбранные</button>
          <button class="btn" data-a="audit" ${inst.loader === 'vanilla' ? 'disabled' : ''} title="Моды оптимизации иногда мешают друг другу. Лаунчер найдёт только то, что точно бесполезно или конфликтует">${ic('eraser')} Проверить на лишние</button>
          ${inst.loader === 'vanilla' ? '<p class="note">Моды работают только в сборках с Fabric, Forge или NeoForge.</p>' : ''}
        </div>
        <div>
          <div class="card"><div class="card-title">${ic('sliders')} Графика игры</div>
            <p class="card-sub">Меняет настройки в options.txt этой сборки. Применится при следующем запуске.</p>
            <div class="row"><button class="btn" data-preset="potato">${ic('zap')} Максимум FPS</button><button class="btn" data-preset="balanced">${ic('star')} Баланс</button></div></div>
          <div class="card"><div class="card-title">${ic('cpu')} Система</div>
            <p class="card-sub">Общие для всех сборок — в «Настройках»:</p>
            <ul class="steps"><li>Флаги сборщика мусора G1 — меньше фризов: <b>${S.config.optimize_jvm ? 'вкл' : 'выкл'}</b></li>
            <li>Повышенный приоритет процесса игры: <b>${S.config.high_priority ? 'вкл' : 'выкл'}</b></li>
            <li>Мощная видеокарта для Java (ноутбуки): <b>${S.config.discrete_gpu ? 'вкл' : 'выкл'}</b></li></ul>
            <button class="btn sm ghost" data-a="settings">Открыть настройки</button></div>
        </div>
      </div>`;
    hydrateIcons(body);
    if (!body.__diag) { body.__diag = true; bindDiag(body, inst); }   // кнопки «Диагностики» (web/js/pages/diag.js)
    // отмечаем уже установленные моды (в том числе пришедшие с импортированной сборкой)
    if (inst.loader !== 'vanilla') {
      const packBtn = $('[data-a=pack]', body);
      packBtn.disabled = true; packBtn.innerHTML = `<span class="spinner"></span> Проверяю, что уже стоит...`;
      api.optimization_status(inst.id).then((r) => {
        if (!body.isConnected) return;
        const have = new Set(r.installed || []);
        $$('#optList .item', body).forEach((row) => {
          if (!have.has(row.dataset.slug)) return;
          const cb = $('input', row); cb.checked = true; cb.disabled = true;
          row.classList.add('done');
          row.insertAdjacentHTML('beforeend', `<span class="chip accent">${ic('check')} установлен</span>`);
          hydrateIcons(row);
        });
        const left = Object.keys(pack).length - have.size;
        packBtn.disabled = left === 0;
        packBtn.innerHTML = left === 0 ? `${ic('check')} Всё уже установлено` : `${ic('download')} Установить выбранные`;
        hydrateIcons(packBtn);
      }).catch(() => { packBtn.disabled = false; packBtn.innerHTML = `${ic('download')} Установить выбранные`; hydrateIcons(packBtn); });
    }
    body.onclick = async (e) => {
      const t = e.target.closest('[data-a],[data-preset]');
      if (!t) return;
      if (t.dataset.preset) { if (okOrToast(await safe(api.apply_preset(inst.id, t.dataset.preset)))) toast('Настройки графики применены'); }
      if (t.dataset.a === 'settings') go('settings');
      if (t.dataset.a === 'atscan') {
        t.disabled = true;
        const r = await safe(api.autotune_scan(inst.id));
        t.disabled = false;
        if (!r || !body.isConnected) return;
        const pc = r.pc, pl = r.plan;
        const gpus = (pc.gpus || []).map((g) => `${esc(g.name)}${g.vram_gb ? ` · ${g.vram_gb} ГБ` : ''}`).join('<br>') || 'не определена';
        $('#atResult', body).innerHTML = `<div class="at-grid">
            <div><small>Память</small><b>${pc.ram_gb || '?'} ГБ</b></div>
            <div><small>Процессор</small><b>${esc(pc.cpu || '—')}</b><span class="faint small">${pc.cores || '?'} потоков</span></div>
            <div><small>Видеокарта</small><b>${gpus}</b></div>
            <div><small>Уровень ПК</small><b class="at-tier ${pl.tier}">${esc(pl.tier_label)}</b></div></div>
          <div class="h2" style="margin-top:12px">Что будет сделано</div>
          <ul class="steps">${pl.why.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>
          <div class="row"><button class="btn primary" data-a="atapply">${ic('check')} Применить</button>
            <span class="small faint">Можно вернуть вручную: память — во вкладке «Настройки», графику — кнопками ниже.</span></div>`;
        hydrateIcons($('#atResult', body));
      }
      if (t.dataset.a === 'atapply') {
        t.disabled = true;
        const r = await safe(api.autotune_apply(inst.id));
        if (!r) { t.disabled = false; return; }
        if (r.error) { t.disabled = false; return toast(r.error, 'error'); }
        await reloadInstances();
        toast(r.mods_started ? `Настройки применены. Ставлю моды оптимизации (${r.mods}) — прогресс внизу` : 'Настройки применены — сработают при следующем запуске', 'info', 7000);
        t.innerHTML = `${ic('check')} Применено`; hydrateIcons(t);
      }
      if (t.dataset.a === 'audit') return openModAudit(inst);
      if (t.dataset.a === 'pack') {
        const slugs = $$('input[data-key]:checked:not(:disabled)', body).map((c) => c.dataset.key);
        if (!slugs.length) return toast('Отметьте хотя бы один мод', 'warn');
        if (okOrToast(await safe(api.install_optimization(inst.id, slugs)))) toast('Устанавливаю моды оптимизации...');
      }
    };
  }

  if (el.__tab === 'packs') {
    body.innerHTML = `<div class="row" style="margin-bottom:10px">
        <button class="btn" data-a="add" data-kind="resourcepack">${ic('plus')} Ресурспак .zip</button>
        <button class="btn" data-a="add" data-kind="shader">${ic('plus')} Шейдер .zip</button>
        <button class="btn" data-a="updates">${ic('refresh')} Обновления</button>
        <button class="btn" data-a="repair" title="Найти ошибки в ресурспаках и шейдерах и починить">${ic('shield')} Починка</button>
        <button class="btn primary" data-a="catalog">${ic('download')} Найти</button></div>
      <div id="packsBody"><div class="row muted"><span class="spinner"></span> Загрузка...</div></div>`;
    hydrateIcons(body);
    let data = await safe(api.packs_list(inst.id)) || { resourcepacks: [], shaders: [], shaders_on: false, running: false };
    const draw = () => {
      const running = data.running ? `<p class="note">Игра этой сборки запущена — менять паки можно после выхода из игры.</p>` : '';
      const rp = data.resourcepacks.map((r) => `
        <div class="item ${r.enabled ? '' : 'off'}" data-n="${esc(r.name)}" data-kind="resourcepack">
          <label class="switch" style="padding:0"><input type="checkbox" data-t ${r.enabled ? 'checked' : ''}><span class="track"></span></label>
          <div class="grow"><div class="title">${esc(r.name)}</div>
            <div class="small faint">${r.is_dir ? 'Папка' : 'Архив'} · ${r.size} КБ${r.error ? ` · <span class="chip">${esc(r.error)}</span>` : ''}</div></div>
          ${r.enabled ? `<button class="btn sm" data-a="up" title="Сделать важнее">Выше</button><button class="btn sm" data-a="down" title="Сделать менее важным">Ниже</button>` : ''}
          <button class="btn sm danger" data-a="del">${ic('trash')}</button></div>`).join('');
      const sh = data.shaders.map((s) => `
        <div class="item ${s.active ? '' : 'off'}" data-s="${esc(s.name)}" data-kind="shader">
          <div class="grow"><div class="title">${esc(s.name)} ${s.active ? '<span class="chip accent">Активен</span>' : ''}</div>
            <div class="small faint">${s.is_dir ? 'Папка' : 'Архив'} · ${s.size} КБ</div></div>
          ${s.active ? '' : `<button class="btn sm primary" data-a="shader">Включить</button>`}
          <button class="btn sm danger" data-a="del">${ic('trash')}</button></div>`).join('');
      $('#packsBody', body).innerHTML = `${running}
        <div class="card" style="margin-bottom:12px"><div class="row"><b class="grow">Ресурспаки</b><span class="small faint">сверху — важнее</span></div>
          <div class="list">${rp || `<div class="empty"><b>Ресурспаков нет</b><span>Нажмите «Найти» или добавьте архив .zip.</span></div>`}</div></div>
        <div class="card"><div class="row"><b class="grow">Шейдеры</b>
            ${data.shaders_on ? `<button class="btn sm" data-a="shader-off">Выключить шейдеры</button>` : ''}</div>
          <p class="small muted">Выбор шейдера работает с модом Iris. Шейдеры OptiFine лаунчер пока не переключает.</p>
          <div class="list">${sh || `<div class="empty"><b>Шейдеров нет</b><span>Нажмите «Найти» или добавьте архив .zip.</span></div>`}</div></div>`;
      hydrateIcons(body);
    };
    const reload = async () => { data = await safe(api.packs_list(inst.id)) || data; draw(); };
    const act = async (...args) => {
      const r = await safe(api.pack_action(inst.id, ...args));
      if (!r || !okOrToast(r)) return;
      if (r.ok) toast(r.ok);
      await reload();
    };
    draw();
    body.onclick = async (e) => {
      const btn = e.target.closest('[data-a]');
      const a = btn?.dataset.a;
      const row = e.target.closest('[data-n],[data-s]');
      const name = row ? (row.dataset.n ?? row.dataset.s) : '';
      const kind = row?.dataset.kind || 'resourcepack';
      if (a === 'add') return act('add', btn.dataset.kind);
      if (a === 'up') return act('move', 'resourcepack', name, -1);
      if (a === 'down') return act('move', 'resourcepack', name, 1);
      if (a === 'shader') return act('shader', 'shader', name);
      if (a === 'shader-off') return act('shader', 'shader', '');
      if (a === 'del' && row) {
        if (await confirmDialog(kind === 'shader' ? 'Удалить шейдер?' : 'Удалить ресурспак?', name, 'Удалить', true)) act('delete', kind, name);
        return;
      }
      if (a === 'updates') return openPackUpdates(inst, btn, reload);
      if (a === 'repair') return openRepair(inst, reload);
      if (a === 'catalog') return go('catalog', { instance: inst.id, type: 'resourcepack' });
    };
    body.onchange = async (e) => {
      const row = e.target.closest('[data-n]');
      if (row && e.target.matches('[data-t]')) act('toggle', 'resourcepack', row.dataset.n, e.target.checked);
    };
  }

  if (el.__tab === 'settings') {
    const ram = inst.ram_gb || 0;
    body.innerHTML = `<div class="card settings">
      <div class="set-grid">
        <label class="field"><span>Название</span><input class="input" data-k="name" value="${esc(inst.name)}"></label>
        <div class="field"><span>Картинка</span><div class="row"><button class="btn" data-a="pic">${ic('image')} ${inst.icon ? 'Сменить картинку' : 'Выбрать картинку'}</button>
          ${inst.icon ? `<button class="btn ghost" data-a="picReset">Убрать</button>` : ''}</div></div>
        <div class="field"><span>Цвет карточки${inst.icon ? ' (виден, если убрать картинку)' : ''}</span><div class="accents" id="instColors">${['#22c55e', '#3b82f6', '#a855f7', '#f97316', '#ec4899', '#14b8a6', '#eab308', '#ef4444']
          .map((c) => `<button style="background:${c}" class="${c === inst.color ? 'on' : ''}" data-c="${c}"></button>`).join('')}</div></div>
      </div>
      <label class="field"><span>Оперативная память: <b id="ramVal">${ramLabel(inst, ram)}</b></span>
        <input type="range" min="0" max="${S.boot.ramMax}" step="1" value="${ram}" data-k="ram_gb"></label>
      <label class="field"><span>Дополнительные аргументы JVM для этой сборки</span><input class="input mono" data-k="jvm_args" value="${esc(inst.jvm_args)}" placeholder="-Dfile.encoding=UTF-8"></label>
      <label class="field"><span>Заметки</span><textarea class="input" rows="2" data-k="notes">${esc(inst.notes)}</textarea></label>
      <div class="row"><button class="btn primary" data-a="save">${ic('check')} Сохранить</button>
        <button class="btn ghost" data-a="reinstall">${ic('refresh')} Проверить файлы игры</button></div></div>`;
    hydrateIcons(body);
    let color = inst.color;
    $('#instColors', body).onclick = (e) => {
      const c = e.target.dataset.c; if (!c) return;
      color = c; $$('#instColors button', body).forEach((b) => b.classList.toggle('on', b.dataset.c === c));
    };
    $('[data-k=ram_gb]', body).oninput = (e) => {
      const v = +e.target.value;
      $('#ramVal', body).textContent = ramLabel(inst, v);
      if (v === 1) e.target.value = 2;
    };
    body.onclick = async (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'save') {
        const values = { color };
        $$('[data-k]', body).forEach((f) => (values[f.dataset.k] = f.value));
        await safe(api.update_instance(inst.id, values));
        await reloadInstances(); toast('Сборка сохранена'); refreshPage();
      }
      if (a === 'pic') pickInstancePicture(inst);
      if (a === 'picReset') setInstancePicture(inst, '');
      if (a === 'reinstall') { if (okOrToast(await safe(api.install_instance(inst.id)))) toast('Проверяю и докачиваю файлы...'); }
    };
  }
}

/* ================================================================= новая сборка */
const LOADER_OPTIONS = [['vanilla', 'Vanilla'], ['fabric', 'Fabric'], ['forge', 'Forge'], ['neoforge', 'NeoForge']];
const LOADER_TYPES = new Set(['fabric', 'forge', 'neoforge']);

function openNewInstance() {
  const cfg = S.config;
  const st = { loader: cfg.version_loader || 'vanilla', page: 0, query: '', selected: null, oldest: !!(cfg.ui || {}).oldestFirst };
  const types = { release: cfg.show_release, snapshot: cfg.show_snapshot, old_beta: cfg.show_beta, old_alpha: cfg.show_alpha };
  const m = openModal(`
    <h3>Новая сборка</h3><p class="muted small">У сборки будет своя папка: моды, миры и настройки не смешаются с другими.</p>
    <div class="set-grid"><label class="field"><span>Название</span><input class="input" id="niName" placeholder="Например: Выживание с друзьями"></label>
      <label class="field"><span>Загрузчик модов</span>${segHtml('loader', LOADER_OPTIONS, st.loader, true)}</label></div>
    <div class="row wrap" style="margin-bottom:10px">
      ${checkHtml('release', 'Релизы', types.release)}${checkHtml('snapshot', 'Снапшоты', types.snapshot)}
      ${checkHtml('old_beta', 'Бета', types.old_beta)}${checkHtml('old_alpha', 'Альфа', types.old_alpha)}
      <div class="grow"></div>${segHtml('order', [['new', 'Сначала новые'], ['old', 'Сначала старые']], st.oldest ? 'old' : 'new')}
      <button class="btn sm ghost" id="niRefresh" title="Обновить список">${ic('refresh')}</button></div>
    <div class="small muted" id="niHint" style="margin:-4px 0 10px"></div>
    <div class="search">${ic('search')}<input class="input" id="niSearch" placeholder="Поиск версии: 1.20, 24w..."></div>
    <div class="spacer"></div>
    <div class="list" id="niList" style="max-height:min(420px,48vh);overflow:auto"></div>
    <div class="pager" id="niPager"></div>
    <div class="row" id="niBuildRow" hidden style="margin-top:10px"><span class="small muted" id="niBuildLabel">Версия загрузчика</span>
      <select class="input" id="niBuild" style="max-width:260px"></select><span class="small faint grow" id="niBuildHint"></span></div>
    <div class="foot"><button class="btn ghost" data-close>Отмена</button><button class="btn primary" id="niCreate" disabled>${ic('plus')} Создать и скачать</button></div>`, { wide: true });

  const load = async () => {
    const t = Object.keys(types).filter((k) => types[k]);
    const res = await safe(api.list_versions(st.loader, t, st.query, 0, 5000, st.oldest));
    if (!res) return;
    const labels = { release: 'Релизы', snapshot: 'Снапшоты', old_beta: 'Бета', old_alpha: 'Альфа' };
    $$('label.check', m).forEach((lb) => {
      const k = $('input', lb)?.dataset.key; if (!labels[k]) return;
      const n = res.counts[k] || 0;
      lb.lastChild.textContent = `${labels[k]} (${n})`;
      lb.style.opacity = n ? '' : '.45';
    });
    $('#niHint', m).textContent = st.loader === 'fabric'
      ? 'Fabric работает только с релизами и снапшотами (примерно с 1.14). Бета и альфа — в Vanilla.'
      : st.loader === 'forge' ? 'Forge — самый большой выбор модов для 1.12.2, 1.16.5 и 1.20.1. Установка дольше, чем у Fabric: лаунчер готовит игру под Forge.'
      : st.loader === 'neoforge' ? 'NeoForge — продолжение Forge для новых версий (1.20.1 и новее). Большинство модов для 1.21+ выходят на нём.'
      : (types.old_alpha || types.old_beta) && !st.oldest ? 'Старые версии (бета, альфа) — в конце списка. Включите «Сначала старые», чтобы увидеть их сразу.' : '';
    if (res.listOk === false) {          // сервер загрузчика не ответил: видны только скачанные версии
      $('#niHint', m).innerHTML = `<span style="color:var(--warn)">${ic('alert')} Список версий не загрузился — показаны только скачанные. Нажмите ${ic('refresh')} справа, чтобы загрузить заново.</span>`;
      hydrateIcons($('#niHint', m));
    }
    const list = $('#niList', m);
    list.innerHTML = res.items.length ? res.items.map((v) => `
      <div class="item ${st.selected?.id === v.id ? 'selected' : ''}" data-v="${esc(v.id)}" data-g="${esc(v.game)}" style="cursor:pointer;padding:8px 12px">
        <span class="chip ${TYPE_CHIP[v.type] || ''}">${esc(v.typeLabel)}</span>
        <div class="grow"><div class="title">${esc(v.game)}</div><div class="small faint">${LOADER_TYPES.has(v.type) ? esc(v.id) + ' · ' : ''}${esc(v.date)}</div></div>
        ${v.installed ? '<span class="chip accent">скачана</span>' : ''}</div>`).join('')
      : `<div class="empty"><b>Ничего не найдено</b><span>${t.length ? 'Проверьте интернет и нажмите «Обновить список»' : 'Отметьте хотя бы один тип версий'}</span></div>`;
    list.scrollTop = 0;
    $('#niPager', m).innerHTML = `<span class="small muted grow">Найдено: ${res.total} — прокрутите список вниз, чтобы увидеть все</span>`;
  };
  bindSegs(m, (name, v) => {
    if (name === 'order') {
      st.oldest = v === 'old'; st.page = 0; (S.config.ui = S.config.ui || {}).oldestFirst = st.oldest; api.save_ui('oldestFirst', st.oldest);
      return load();
    }
    st.loader = v; st.page = 0; st.selected = null; $('#niCreate', m).disabled = true; $('#niBuildRow', m).hidden = true;
    api.save_settings({ version_loader: v }); S.config.version_loader = v; load();
  });
  m.addEventListener('change', (e) => {
    const k = e.target.dataset?.key; if (!k) return;
    types[k] = e.target.checked; st.page = 0;
    const map = { release: 'show_release', snapshot: 'show_snapshot', old_beta: 'show_beta', old_alpha: 'show_alpha' };
    S.config[map[k]] = e.target.checked; api.save_settings({ [map[k]]: e.target.checked });
    load();
  });
  $('#niSearch', m).oninput = debounce((e) => { st.query = e.target.value; st.page = 0; load(); });
  $('#niList', m).onclick = (e) => {
    const row = e.target.closest('[data-v]'); if (!row) return;
    st.selected = { id: row.dataset.v, game: row.dataset.g };
    $$('#niList .item', m).forEach((r) => r.classList.toggle('selected', r === row));
    $('#niCreate', m).disabled = false;
    const prefix = { fabric: 'Fabric ', forge: 'Forge ', neoforge: 'NeoForge ' }[st.loader] || '';
    if (!$('#niName', m).value) $('#niName', m).placeholder = prefix + row.dataset.g;
    // Forge новее 1.20.1: моды почти все ушли на NeoForge — подсказываем сразу, до создания сборки
    const [, mi = 0, pa = 0] = row.dataset.g.split('.').map((x) => +x || 0);
    const yearBased = !row.dataset.g.startsWith('1.');      // 26.1, 26.3 … — новая нумерация Mojang
    const lateForge = st.loader === 'forge' && (yearBased || mi > 20 || (mi === 20 && pa >= 2));
    $('#niHint', m).innerHTML = lateForge
      ? `<span style="color:var(--warn)">${ic("alert")} ${esc(row.dataset.g)}: почти все моды выходят на NeoForge, а не на Forge — лучше выберите NeoForge.</span>` : $('#niHint', m).innerHTML;
    hydrateIcons($('#niHint', m));
    loadBuilds();
  };
  // выбор версии Forge/NeoForge (по умолчанию — рекомендованная)
  const loadBuilds = async () => {
    const rowEl = $('#niBuildRow', m);
    if (st.loader !== 'forge' && st.loader !== 'neoforge') { rowEl.hidden = true; return; }
    const game = st.selected.game;
    const builds = await safe(api.loader_builds(st.loader, game)) || [];
    if (st.selected?.game !== game) return;
    rowEl.hidden = !builds.length;
    $('#niBuildLabel', m).textContent = st.loader === 'forge' ? 'Версия Forge' : 'Версия NeoForge';
    const sel = $('#niBuild', m);
    sel.innerHTML = builds.slice(0, 60).map((b) => `<option value="${esc(b.id)}" ${b.id === st.selected.id ? 'selected' : ''}>${esc(b.version)}${b.recommended ? ' (рекомендуется)' : ''}</option>`).join('');
    $('#niBuildHint', m).textContent = builds.length > 1 ? 'Если сборка модов просит конкретную версию — выберите её.' : '';
    sel.onchange = () => { st.selected.id = sel.value; };
  };
  $('#niRefresh', m).onclick = () => {
    api.refresh_versions(); toast('Обновляю список версий...');
    $('#niList', m).innerHTML = '<div class="row muted" style="padding:12px"><span class="spinner"></span> Загружаю список версий...</div>';
  };
  $('#niCreate', m).onclick = async () => {
    const btn = $('#niCreate', m);
    if (btn.disabled || !st.selected) return;          // двойной щелчок создавал две одинаковые сборки
    btn.disabled = true;
    const name = $('#niName', m).value.trim() || $('#niName', m).placeholder;
    const inst = await safe(api.create_instance(name, st.selected.id, st.selected.game, st.loader));
    if (!inst || inst.error) { btn.disabled = false; if (inst?.error) toast(inst.error, 'error'); return; }
    m.close(); await reloadInstances(); selectInstance(inst.id);
    toast(`Сборка «${inst.name}» создана — скачиваю игру`);
    if (S.page === 'instances' || S.page === 'lobby') refreshPage();
  };
  m.__reload = load;
  window.__versionsModal = m;
  load();
}

/* ================================================================= быстрый выбор сборки */
// меню «Поделиться / экспорт» — одно и то же на странице сборки и в окне «Выберите сборку»
function openExportMenu(anchor, inst) {
  const dd = openDropdown(anchor, `<div class="dd-title">ПОДЕЛИТЬСЯ</div>
    <button class="dd-item" data-f="code"><span class="fmt ne">${ic('link')}</span><div><b>Код для друга</b><small>6 символов — друг вставит его в «Сборки → По коду»</small></div></button>
    <div class="dd-title">ЭКСПОРТ В ФАЙЛ</div>
    <button class="dd-item" data-f="mrpack"><span class="fmt mr">${ic('box')}</span><div><b>Modrinth (mrpack)</b><small>Prism, Modrinth App, ATLauncher</small></div></button>
    <button class="dd-item" data-f="curseforge"><span class="fmt cf">${ic('archive')}</span><div><b>CurseForge (zip)</b><small>CurseForge App и другие лаунчеры</small></div></button>
    <button class="dd-item" data-f="vault"><span class="fmt ne">${ic('zap')}</span><div><b>VaultMC (vaultpack)</b><small>Для друзей с VaultMC</small></div></button>`, 'right');
  dd.onclick = async (ev) => {
    const f = ev.target.closest('[data-f]')?.dataset.f; if (!f) return;
    if (f === 'code') { closeDropdown(); return shareInstanceCode(inst); }
    closeDropdown(); toast('Готовлю файл сборки...');
    const r = await safe(api.export_instance(inst.id, f));
    if (r?.path) toast('Сохранено: ' + r.path, 'info', 7000);
  };
}

function openInstancePicker() {
  if (!S.instances.length) return openNewInstance();
  const m = openModal(`<h3>Выберите сборку</h3><div class="spacer"></div>
    <div class="inst-grid" style="grid-template-columns:1fr 1fr">${S.instances.map((i) => instCardHtml(i, i.id === S.selected?.id)).join('')}</div>
    <div class="foot"><button class="btn ghost" data-imp>${ic('download')} Импорт сборки</button>
      <button class="btn ghost" data-code>${ic('link')} По коду</button>
      <button class="btn ghost" data-new>${ic('plus')} Новая сборка</button></div>`, { wide: true });
  bindInstCards(m);
  $$('.inst-card', m).forEach((c) => c.addEventListener('click', (e) => { if (!e.target.closest('[data-q]')) { m.close(); refreshPage(); } }));
  $('[data-new]', m).onclick = () => { m.close(); openNewInstance(); };
  $('[data-code]', m).onclick = () => { m.close(); openImportByCode(); };
  $('[data-imp]', m).onclick = async () => {
    m.close();
    const r = await safe(api.import_instance());
    if (r && okOrToast(r) && r.started) toast('Импортирую сборку — прогресс внизу');
  };
}

async function reloadInstances() {
  S.instances = (await safe(api.list_instances())) || [];
  renderTitleInstance();
  renderPlaybar();
}

/* ================================================================= картинка и имя сборки */
function instancePictureMenu(anchor, inst) {
  const dd = openDropdown(anchor, `<div class="dd-title">КАРТИНКА СБОРКИ</div>
    <button class="dd-item" data-p="pick">${ic('image')}<b>Выбрать картинку...</b></button>
    ${inst.icon ? `<button class="dd-item" data-p="reset">${ic('refresh')}<b>Вернуть пиксельную иконку</b></button>` : ''}`, 'left');
  dd.onclick = (e) => {
    const p = e.target.closest('[data-p]')?.dataset.p; if (!p) return;
    closeDropdown();
    if (p === 'pick') pickInstancePicture(inst);
    if (p === 'reset') setInstancePicture(inst, '');
  };
}

// выбор картинки: обрезаем по центру в квадрат 128×128 прямо в интерфейсе — в сборку кладётся маленький PNG
function pickInstancePicture(inst) {
  const input = document.createElement('input');
  input.type = 'file'; input.accept = 'image/png,image/jpeg,image/webp,image/gif,image/bmp';
  input.onchange = () => {
    const file = input.files && input.files[0]; if (!file) return;
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
      const side = Math.min(img.width, img.height);
      const c = document.createElement('canvas'); c.width = c.height = 128;
      const ctx = c.getContext('2d');
      ctx.imageSmoothingEnabled = side > 128;  // маленькие пиксельные картинки не размываем
      ctx.drawImage(img, (img.width - side) / 2, (img.height - side) / 2, side, side, 0, 0, 128, 128);
      URL.revokeObjectURL(url);
      setInstancePicture(inst, c.toDataURL('image/png'));
    };
    img.onerror = () => { URL.revokeObjectURL(url); toast('Не получилось открыть картинку', 'error'); };
    img.src = url;
  };
  input.click();
}

async function setInstancePicture(inst, dataUrl) {
  const r = await safe(api.set_instance_icon(inst.id, dataUrl));
  if (!r || !okOrToast(r)) return;
  await reloadInstances(); refreshPage(); renderPlaybar(); renderTitleInstance();
  toast(dataUrl ? 'Картинка сборки обновлена' : 'Вернул пиксельную иконку');
}

function renameInstance(inst) {
  const m = openModal(`<h3>Переименовать сборку</h3>
    <label class="field"><span>Название</span><input class="input" id="rnName" maxlength="60" value="${esc(inst.name)}"></label>
    <div class="foot"><button class="btn ghost" data-close>Отмена</button><button class="btn primary" data-ok>${ic('check')} Сохранить</button></div>`);
  const input = $('#rnName', m); input.focus(); input.select();
  const save = async () => {
    const name = input.value.trim();
    if (!name) return toast('Введите название', 'warn');
    m.close();
    if (name === inst.name) return;
    await safe(api.update_instance(inst.id, { name }));
    await reloadInstances(); refreshPage(); renderPlaybar(); renderTitleInstance();
    toast('Сборка переименована');
  };
  m.querySelector('[data-ok]').onclick = save;
  input.onkeydown = (e) => { if (e.key === 'Enter') save(); };
}

// сколько памяти получит сборка: так же считает лаунчер при запуске (backend/api.py → _auto_ram)
function autoRam(inst) {
  const global = S.config.ram_gb, mods = inst.modsCount || 0, total = S.boot.ramTotal || 0;
  const want = mods >= 120 ? 8 : mods >= 40 ? 6 : 0;
  return want > global && total >= want + 4 ? want : global;
}
function ramLabel(inst, ram) {
  if (ram) return ram + ' ГБ';
  const auto = autoRam(inst);
  return auto > S.config.ram_gb ? `авто: ${auto} ГБ (${inst.modsCount} модов, больше общих ${S.config.ram_gb} ГБ)`
    : `как в общих настройках (${auto} ГБ)`;
}

/* ================================================================= обновления модов */
async function openModUpdates(inst, btn) {
  const old = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Проверяю...';
  const res = await safe(api.check_mod_updates(inst.id));
  btn.disabled = false; btn.innerHTML = old; hydrateIcons(btn);
  if (!res) return;
  const ups = res.updates || [];
  if (!ups.length) return toast('Все моды последних версий');
  const majors = ups.filter((u) => u.major);
  const m = openModal(`<h3>Обновления модов</h3>
    ${majors.length ? `<p class="note">${ic('alert')} Крупных обновлений: ${majors.length}. У них выросло первое число версии (например, 2.x → 3.x): так авторы отмечают, что настройки и данные могут перестать подходить — после такого обновления игра иногда не запускается или показывает чёрный экран. Они не отмечены: обновляйте по одному и только если нужно. Старая версия сохранится — вернуть её можно кнопкой «Откат».</p>` : ''}
    <p class="muted small">Найдено ${ups.length} — под Minecraft ${esc(inst.game_version)} и ${esc(inst.loaderLabel || inst.loader)}. Старые файлы заменятся новыми, выключенные моды останутся выключенными. Старые версии сохраняются — вернуть их можно кнопкой «Откат».</p>
    <div class="row" style="margin:8px 0">${checkHtml('all', 'Выбрать все', true)}</div>
    <div class="list" id="updList" style="max-height:360px;overflow:auto">${ups.map((u, n) => `
      <div class="item" style="padding:8px 12px">${checkHtml('u' + n, '', !u.major)}
        <div class="grow"><div class="title">${esc(u.title)} ${u.major ? '<span class="chip sand">Крупное обновление</span>' : ''}</div>
          <div class="small faint">${esc(u.file.replace(/\.disabled$/, ''))} → ${esc(u.new_file)}</div>
          ${u.major ? `<div class="small" style="margin-top:2px">Версия ${esc(u.old_version || '?')} → ${esc(u.version || '?')}: настройки могут не подойти новой версии.</div>` : ''}
          ${u.version_id ? `<button class="btn sm ghost upd-cl-btn" data-cl="${n}">${ic('file')} Что нового</button><div class="upd-cl small" hidden></div>` : ''}</div>
        <span class="chip">${u.source === 'modrinth' ? 'Modrinth' : 'CurseForge'}</span></div>`).join('')}</div>
    <div class="foot"><button class="btn ghost" data-close>Позже</button>
      <button class="btn primary" id="updGo">${ic('download')} Обновить выбранные</button></div>`, { wide: true });
  const boxes = () => $$('#updList input[type=checkbox]', m);
  m.addEventListener('change', (e) => {
    if (e.target.dataset.key === 'all') boxes().forEach((b) => (b.checked = e.target.checked));
    $('#updGo', m).disabled = !boxes().some((b) => b.checked);
  });
  // «Что нового»: список изменений версии подгружается только по клику (лишних запросов к Modrinth/CurseForge нет)
  m.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-cl]');
    if (!btn) return;
    const box = btn.nextElementSibling;
    if (!box.hidden) { box.hidden = true; return; }
    box.hidden = false;
    if (box.dataset.loaded) return;
    const u = ups[+btn.dataset.cl];
    box.textContent = 'Загружаю...';
    const r = await api.mod_changelog(u.source, u.project_id, u.version_id).catch(() => null);
    box.dataset.loaded = '1';
    box.textContent = !r ? 'Нет связи' : r.error ? r.error : (r.text || 'Автор мода не написал, что изменилось');
  });
  $('#updGo', m).onclick = async () => {
    const chosen = ups.filter((_, n) => boxes()[n].checked);
    const risky = chosen.filter((u) => u.major);
    if (risky.length && !(await confirmDialog('Крупные обновления модов',
        `${risky.map((u) => `${u.title} ${u.old_version || '?'} → ${u.version || '?'}`).join('\n')}\n\nПосле такого обновления настройки мода могут не подойти, и игра иногда не запускается. Старая версия сохранится — её можно вернуть кнопкой «Откат». Обновить?`,
        'Всё равно обновить', true))) return;
    if (okOrToast(await safe(api.apply_mod_updates(inst.id, chosen)))) { m.close(); toast(`Обновляю моды: ${chosen.length}`); }
  };
}

/* ---------------------------------------------------------------- обновления ресурспаков и шейдеров */
async function openPackUpdates(inst, btn, onDone) {
  const old = btn.innerHTML;
  btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Проверяю...';
  const res = await safe(api.packs_check_updates(inst.id));
  btn.disabled = false; btn.innerHTML = old; hydrateIcons(btn);
  if (!res) return;
  const ups = res.updates || [];
  if (!ups.length) return toast('Ресурспаки и шейдеры последних версий');
  const m = openModal(`<h3>Обновления ресурспаков и шейдеров</h3>
    <p class="muted small">Найдено ${ups.length} — под Minecraft ${esc(inst.game_version)}. Старый файл сохранится в папке .vaultmc_packbackup внутри сборки.</p>
    <div class="row" style="margin:8px 0">${checkHtml('all', 'Выбрать все', true)}</div>
    <div class="list" style="max-height:360px;overflow:auto">${ups.map((u, n) => `
      <div class="item" style="padding:8px 12px">${checkHtml('u' + n, '', true)}
        <div class="grow"><div class="title">${esc(u.title)} <span class="chip">${u.kind === 'shader' ? 'Шейдер' : 'Ресурспак'}</span></div>
          <div class="small faint">${esc(u.file)} → ${esc(u.new_file)}${u.version ? ' · ' + esc(u.version) : ''}</div></div></div>`).join('')}</div>
    <div class="foot"><button class="btn ghost" data-close>Позже</button>
      <button class="btn primary" id="pkGo">${ic('download')} Обновить выбранные</button></div>`, { wide: true });
  const boxes = () => $$('input[type=checkbox]', m).filter((b) => b.dataset.key !== 'all');
  m.addEventListener('change', (e) => {
    if (e.target.dataset.key === 'all') boxes().forEach((b) => (b.checked = e.target.checked));
    $('#pkGo', m).disabled = !boxes().some((b) => b.checked);
  });
  $('#pkGo', m).onclick = async () => {
    const chosen = ups.filter((_, n) => boxes()[n]?.checked);
    if (!chosen.length) return;
    if (okOrToast(await safe(api.packs_apply_updates(inst.id, chosen)))) { m.close(); toast(`Обновляю: ${chosen.length}`); }
  };
}

/* ---------------------------------------------------------------- откат модов */
async function openModRollback(inst, onDone) {
  const list = await safe(api.mod_backups(inst.id)) || [];
  if (!list.length) return toast('Откатывать нечего: после обновления модов здесь появятся старые версии');
  const when = (t) => new Date(t * 1000).toLocaleString();
  const m = openModal(`<h3>Откат модов</h3>
    <p class="muted small">Старые версии, которые лаунчер сохранил при обновлении. Нажмите «Вернуть» — новая версия мода заменится старой.</p>
    <div class="list" id="rbList" style="max-height:360px;overflow:auto">${list.map((b) => `
      <div class="item" style="padding:8px 12px" data-id="${esc(b.id)}">
        <div class="grow"><div class="title">${esc(b.title)}</div>
          <div class="small faint">${esc(b.new_file.replace(/\.disabled$/, ''))} → ${esc(b.old_file.replace(/\.disabled$/, ''))} · ${esc(when(b.at))}</div></div>
        <button class="btn sm" data-rb>Вернуть</button></div>`).join('')}</div>
    <div class="foot"><button class="btn primary" data-close>Закрыть</button></div>`, { wide: true });
  m.addEventListener('click', async (e) => {
    const btn = e.target.closest('[data-rb]');
    if (!btn) return;
    const row = btn.closest('.item');
    btn.disabled = true;
    const r = await safe(api.rollback_mod(inst.id, row.dataset.id));
    if (r && r.error) { btn.disabled = false; return toast(r.error, 'error', 6000); }
    if (r && r.ok) { toast(`Вернул старую версию: ${r.title}`); row.remove(); onDone && onDone(); if (!$('#rbList .item', m)) m.close(); }
    else btn.disabled = false;
  });
}

/* ---------------------------------------------------------------- починка сборки */
async function openRepair(inst, onDone) {
  const m = openModal(`<h3>Починка сборки «${esc(inst.name)}»</h3>
    <p class="small" style="margin:0 0 10px;color:var(--warn)">${ic('alert')} Нужна, только если Minecraft не запускается или вылетает. Если игра работает — ничего здесь не трогайте.</p>
    <div class="muted small" id="rpBody"><span class="spinner"></span> Проверяю моды, шейдеры, ресурспаки и журнал последнего запуска...</div>`, { wide: true });
  const body = $('#rpBody', m);
  const LV = { error: ['alert', 'Ошибка'], warn: ['alert', 'Подозрительно'], info: ['file', 'К сведению'] };
  const scan = async () => {
    body.innerHTML = '<span class="spinner"></span> Проверяю...';
    const r = await safe(api.repair_scan(inst.id));
    if (!m.isConnected) return;
    if (!r || r.error) { body.innerHTML = `<p class="note">${esc(r?.error || 'Не удалось проверить сборку')}</p>`; return; }
    const items = r.items;
    const fixable = items.filter((i) => i.fix && i.level !== 'info');
    if (!items.length) {
      body.innerHTML = `<div class="crash-item"><div class="grow"><b>${ic('check')} Проблем в модах не найдено</b>
        <p class="small muted">Лаунчер проверил ${r.mods} мод(ов): загрузчик, версии игры, зависимости, дубли, известные конфликты, шейдеры, ресурспаки и журнал. Если экран всё равно чёрный — попробуйте сбросить картинку: выключатся шейдеры и ваши ресурспаки (копия options.txt сохранится).</p></div>
        <button class="btn" data-a="reset">Сбросить шейдеры и ресурспаки</button></div>
        <div class="crash-item"><div class="grow"><b>Режим совместимости</b>
        <p class="small muted">Игра запустится без оптимизаций лаунчера (флаги сборщика мусора, кэш классов, смена приоритета, полный экран), как в обычных лаунчерах. Помогает, если в другом лаунчере сборка работает, а здесь нет (например, чёрный экран после заставки).</p></div>
        <button class="btn" data-a="compat">Включить</button></div>
        <div class="foot"><div class="grow"></div><button class="btn primary" data-close>Закрыть</button></div>`;
      body.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
      return;
    }
    const anyBox = items.some((i) => i.fix && i.level !== 'info');
    body.innerHTML = `<p class="muted small">Найдено: ${items.length}. ${anyBox ? 'Отметьте, что чинить — моды при этом только выключаются (файл остаётся, включить обратно можно во вкладке «Моды»).' : 'Это подсказки: ничего не сломано, но можно проверить кнопками ниже. Моды при этом только выключаются (файл остаётся, включить обратно можно во вкладке «Моды»).'}</p>
      <div class="crash-list" style="max-height:380px;overflow:auto">${items.map((i, n) => `
        <div class="crash-item"><label class="row" style="gap:10px;align-items:flex-start;flex:1;cursor:default">
          ${i.fix && i.level !== 'info' ? `<input type="checkbox" data-id="${esc(i.id)}" ${i.level === 'error' || i.id.startsWith('group:') || i.id.startsWith('filever:') || i.id === 'shaders_on' || i.id === 'compat_hint' ? 'checked' : ''}>` : ''}
          <div class="grow"><b>${esc(i.title)}</b> <span class="chip">${LV[i.level][1]}</span>
            <p class="small muted">${esc(i.text)}</p>
            ${i.fix && i.level !== 'info' ? `<p class="small faint">Исправление: ${esc(i.fix.label)}</p>` : ''}
            ${i.level === 'info' && i.fix ? `<div class="row" style="gap:8px;flex-wrap:wrap;margin-top:8px">
              ${i.alt ? `<button class="btn sm primary" data-cmp="${n}">${ic('search')} Что лучше выключить?</button>` : ''}
              <button class="btn sm" data-do="${n}">${esc(i.fix.label)}</button>
              ${i.alt ? `<button class="btn sm" data-alt="${n}">${esc(i.alt.label)}</button>` : ''}</div>` : ''}
            ${i.level !== 'info' && i.alt ? `<button class="btn sm ghost" data-alt="${n}" style="margin-top:6px">Или: ${esc(i.alt.label)}</button>` : ''}</div></label></div>`).join('')}</div>
      <div class="foot"><div class="grow"></div><button class="btn" data-close>Закрыть</button>
        ${fixable.length ? `<button class="btn primary" data-a="apply">${ic('check')} Починить выбранное</button>` : ''}</div>`;
    hydrateIcons(body);
    body.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));   // кнопки появились после открытия окна
    body.onclick = async (e) => {
      const cmp = e.target.closest('[data-cmp]');
      if (cmp) { const it = items[+cmp.dataset.cmp]; return openCompare(inst, it.fix.arg, it.alt.arg, () => { onDone && onDone(); scan(); }); }
      const alt = e.target.closest('[data-alt],[data-do]');
      if (alt) {
        alt.disabled = true;
        const fix = alt.dataset.alt !== undefined ? items[+alt.dataset.alt].alt : items[+alt.dataset.do].fix;
        const res = await safe(api.crash_fix(inst.id, fix.action, fix.arg));
        if (res && res.ok) { toast(res.ok); onDone && onDone(); scan(); } else { alt.disabled = false; if (res) toast(res.error, 'error'); }
        return;
      }
      const act = e.target.closest('[data-a]')?.dataset.a;
      if (act === 'apply') {
        const ids = [...body.querySelectorAll('input[data-id]:checked')].map((c) => c.dataset.id);
        if (!ids.length) return toast('Ничего не выбрано', 'warn');
        e.target.closest('[data-a]').disabled = true;
        const res = await safe(api.repair_apply(inst.id, ids));
        if (res && res.error) { toast(res.error, 'error', 6000); return scan(); }
        if (res) toast(res.failed.length ? `Починено: ${res.done.length}, не удалось: ${res.failed.length}` : `Починено: ${res.done.length}. Нажмите «Играть»`, res.failed.length ? 'warn' : 'info', 6000);
        onDone && onDone();
        scan();
      }
    };
  };
  m.addEventListener('click', async (e) => {
    if (e.target.closest('[data-close]')) return m.close();   // кнопки «Закрыть» появляются после открытия окна
    if (e.target.closest('[data-a="compat"]')) {
      const res = await safe(api.crash_fix(inst.id, 'compat_on', ''));
      if (res) toast(res.ok || res.error, res.ok ? 'info' : 'error', 6000);
      return;
    }
    if (e.target.closest('[data-a="reset"]')) {
      const res = await safe(api.repair_reset_visuals(inst.id));
      if (res) toast(res.ok || res.error, res.ok ? 'info' : 'error', 6000);
    }
  });
  scan();
}

/* Сравнение двух конфликтующих модов: что делает каждый и какой выключить с меньшими потерями */
async function openCompare(inst, fileA, fileB, onDone) {
  const m = openModal(`<h3>Какой мод выключить?</h3><div id="cmpBody" class="muted small"><span class="spinner"></span> Сравниваю...</div>`, { wide: true });
  const r = await safe(api.repair_compare(inst.id, fileA, fileB));
  const body = $('#cmpBody', m);
  if (!m.isConnected) return;
  m.addEventListener('click', (e) => { if (e.target.closest('[data-close]')) m.close(); });
  if (!r || r.error) { body.innerHTML = `<p class="note">${esc(r?.error || 'Не удалось сравнить')}</p><div class="foot"><div class="grow"></div><button class="btn primary" data-close>Закрыть</button></div>`; return; }
  const side = (x) => `<div class="card" style="flex:1;min-width:0"><b>${esc(x.name)}</b> <span class="small faint">${esc(x.version)}</span>
      <p class="small muted" style="margin:6px 0">${x.description ? esc(x.description) : 'Автор не оставил описание'}</p>
      <div class="small faint">Правок игры: ${x.mixins} · ${x.size_kb} КБ</div>
      <div class="small faint">${x.dependents.length ? 'Нужен модам: ' + esc(x.dependents.join(', ')) : 'Другие моды от него не зависят'}</div></div>`;
  const drop = r.mods.find((x) => x.file === r.disable);
  body.className = '';
  body.innerHTML = `<div class="row" style="gap:12px;align-items:stretch">${r.mods.map(side).join('')}</div>
    <p style="margin:12px 0 4px"><b>${drop ? 'Предлагаю выключить: ' + esc(drop.name) : 'Однозначного ответа нет'}</b></p>
    <p class="small muted">${esc(r.reason)}</p>${r.note ? `<p class="small faint">Предположение: ${esc(r.note)}</p>` : ''}
    <div class="foot"><div class="grow"></div><button class="btn" data-close>Закрыть</button>
      ${drop ? `<button class="btn primary" id="cmpGo">${ic('check')} Выключить ${esc(drop.name)}</button>` : ''}</div>`;
  hydrateIcons(body);
  const go1 = $('#cmpGo', m);
  if (go1) go1.onclick = async () => {
    go1.disabled = true;
    const res = await safe(api.crash_fix(inst.id, 'disable_mod', r.disable));
    if (res && res.ok) { toast(res.ok); m.close(); onDone && onDone(); } else { go1.disabled = false; if (res) toast(res.error, 'error'); }
  };
}
