// Иконка проекта; если её нет или она не загрузилась — цветная плитка с первой буквой названия
const iconHue = (t) => [...(t || '?')].reduce((a, c) => (a * 31 + c.charCodeAt(0)) % 360, 7);
const iconTile = (t) => `<div class="icon ph" style="--h:${iconHue(t)}">${esc((t || '?').trim().charAt(0).toUpperCase())}</div>`;
const modIcon = (h) => h.icon_url
  ? `<img class="icon" loading="lazy" src="${esc(h.icon_url)}" alt="" data-title="${esc(h.title)}" onerror="this.outerHTML=iconTile(this.dataset.title)">`
  : iconTile(h.title);
window.iconTile = iconTile;
/* Каталог: Modrinth / CurseForge, моды / ресурспаки / шейдеры / сборки, пакетная установка галочками. */

const CAT_TYPES = [['mod', 'Моды'], ['resourcepack', 'Ресурсы'], ['shader', 'Шейдеры'], ['modpack', 'Сборки']];

registerPage('catalog', {
  render(el, params = {}) {
    const ui = S.config.ui || {};
    el.__st = {
      source: params.source || ui.catSource || S.config.mod_source || 'modrinth',
      type: params.query ? 'mod' : params.type || ui.catType || 'mod',
      sort: ui.catSort || 'Популярные',
      query: params.query || '', page: 0, pages: 1, picked: new Map(), items: [],
      instance: params.instance || S.selected?.id || '',
      lock: !!params.lock,             // «Сборки → Готовые сборки»: только сборки, без вкладок «Моды/Ресурсы/Шейдеры»
    };
    if (params.instance) selectInstance(params.instance);
    this.refresh(el);
  },
  refresh(el) {
    const st = el.__st;
    const inst = S.instances.find((i) => i.id === st.instance);
    el.innerHTML = `
      <div class="cat-top">
        ${st.lock ? `<button class="btn ghost sm" data-a="back">← Мои сборки</button><b class="cat-lock-title">Готовые сборки</b>` : ''}
        ${segHtml('source', [['modrinth', 'Modrinth'], ['curseforge', 'CurseForge']], st.source)}
        ${st.lock ? '' : segHtml('type', CAT_TYPES, st.type)}
        <div class="grow"></div>
        <div class="cat-ver">${st.type === 'modpack'
          ? `<span class="small muted">Сборка станет новой сборкой лаунчера</span>`
          : inst ? `<span class="small muted">Сборка</span>${inst.name.includes(inst.game_version) || inst.name.toLowerCase().includes(inst.loaderLabel.toLowerCase()) ? '' : `<span class="chip">${esc(inst.name)}</span>`}<span class="chip sand">${esc(inst.loaderLabel)} ${esc(inst.game_version)}</span>`
            : '<span class="small muted">Нет сборки</span>'}
          ${st.type === 'modpack' || !inst ? '' : `<button class="btn sm" data-a="updates" title="Проверить и обновить моды этой сборки">${ic('refresh')} Обновления</button><button class="btn sm" data-a="rollback" title="Вернуть мод к версии до обновления">${ic('refresh')} Откат</button>`}
          ${st.type === 'modpack' ? '' : `<button class="btn sm primary" data-a="pick">Сменить</button>`}</div>
      </div>
      <div class="search">${ic('search')}<input class="input" id="catSearch" placeholder="${st.type === 'modpack' ? 'Поиск сборки: Fabulously Optimized, RLCraft...' : 'Поиск: sodium, journeymap, faithful...'}" value="${esc(st.query)}"></div>
      <div class="spacer"></div>
      <div class="cat-body">
        <div class="card filters">
          <div class="h2">Сортировка</div>
          <div class="pills" id="sorts">${S.boot.sorts.map((s) => `<button class="${s === st.sort ? 'on' : ''}" data-s="${esc(s)}">${esc(s)}</button>`).join('')}</div>
          <div class="h2">Подсказка</div>
          <p class="small muted" style="line-height:1.5;margin:0">Отметьте несколько проектов галочками и установите пачкой. Обязательные зависимости (например, Fabric API) ставятся сами.</p>
          ${st.source === 'curseforge' && !hasCfKey() ? `<div class="spacer"></div><p class="small" style="color:var(--warn);line-height:1.5">Для CurseForge нужен бесплатный API-ключ.</p><button class="btn sm" data-a="cfkey">Указать ключ</button>` : ''}
        </div>
        <div><div class="list" id="catList"></div><div class="pager" id="catPager"></div><div id="batch"></div></div>
      </div>`;
    hydrateIcons(el);
    bindSegs(el, (name, v) => {
      st[name] = v; st.page = 0; st.picked.clear();
      api.save_ui(name === 'source' ? 'catSource' : 'catType', v);
      if (name === 'source') { S.config.mod_source = v; api.save_settings({ mod_source: v }); }
      (S.config.ui = S.config.ui || {})[name === 'source' ? 'catSource' : 'catType'] = v;
      this.refresh(el);
    });
    $('#catSearch', el).oninput = debounce((e) => { st.query = e.target.value; st.page = 0; loadCatalog(el); }, 350);
    $('#sorts', el).onclick = (e) => {
      const s = e.target.dataset.s; if (!s) return;
      st.sort = s; st.page = 0; (S.config.ui = S.config.ui || {}).catSort = s; api.save_ui('catSort', s);
      $$('#sorts button', el).forEach((b) => b.classList.toggle('on', b.dataset.s === s));
      loadCatalog(el);
    };
    el.onclick = (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'pick') pickCatalogInstance(el);
      const curInst = S.instances.find((i) => i.id === el.__st.instance);
      if (a === 'updates' && curInst) openModUpdates(curInst, e.target.closest('[data-a]'));
      if (a === 'rollback' && curInst) openModRollback(curInst, () => loadCatalog(el));
      if (a === 'cfkey') go('settings', { focus: 'cf' });
      if (a === 'back') go('instances');
    };
    loadCatalog(el);
  },
});

// «Сборки → Готовые сборки»: тот же каталог, но только сборки и внутри вкладки «Сборки» (раньше кнопка уводила во вкладку «Моды»)
registerPage('packs', {
  render(el) { PAGES.catalog.render(el, { type: 'modpack', lock: true }); },
  refresh(el) { PAGES.catalog.refresh(el); },
});

async function loadCatalog(el) {
  const st = el.__st;
  const list = $('#catList', el);
  if (!list) return;
  list.innerHTML = Array.from({ length: 5 }, () => '<div class="item" style="height:76px;opacity:.5"><div class="icon"></div><div class="grow"><div class="spinner"></div></div></div>').join('');
  if (st.source === 'curseforge' && !hasCfKey()) {
    list.innerHTML = `<div class="key-card"><b>CurseForge требует свой ключ</b>
      <p class="small muted" style="line-height:1.5;margin:8px 0 0">CurseForge пускает в свой каталог только по ключу. В больших лаунчерах (Prism и др.)
      его встроил разработчик, а в эту сборку VaultMC ключ ещё не встроен — можно вставить свой. Он бесплатный, делается за 2 минуты.</p>
      <ol><li>Откройте <a href="#" data-url="https://console.curseforge.com/">console.curseforge.com</a> и войдите (можно через Google).</li>
      <li>Слева выберите <b>API keys</b> и скопируйте ключ.</li><li>Вставьте его сюда:</li></ol>
      <div class="row"><input class="input mono grow" id="cfKey" placeholder="$2a$10$..."><button class="btn primary" id="cfSave">Сохранить</button></div>
      <p class="note">Без ключа всё то же самое есть в Modrinth — большинство модов лежат и там.</p></div>`;
    $('#catPager', el).innerHTML = '';
    $$('[data-url]', list).forEach((a) => (a.onclick = (e) => { e.preventDefault(); api.open_url(a.dataset.url); }));
    $('#cfSave', list).onclick = async () => {
      const v = $('#cfKey', list).value.trim();
      if (v.length < 20) return toast('Ключ слишком короткий — скопируйте его целиком', 'warn');
      await saveSetting({ curseforge_api_key: v }); toast('Ключ CurseForge сохранён'); PAGES.catalog.refresh(el); hydrateIcons(el);
    };
    return;
  }
  const token = (el.__token = (el.__token || 0) + 1);
  let res;
  try {
    res = await api.catalog_search(st.source, st.type, st.query, st.sort, st.page, st.type === 'modpack' ? '' : st.instance);
  } catch (e) {
    if (token !== el.__token) return;
    list.innerHTML = `<div class="empty"><b>Каталог недоступен</b><span>${esc(e.message || e)}</span></div>`;
    $('#catPager', el).innerHTML = '';
    return;
  }
  if (token !== el.__token) return;  // пришёл устаревший ответ
  st.items = res.items; st.pages = res.pages;
  const inst = S.instances.find((i) => i.id === st.instance);
  const needFabric = st.type === 'mod' && inst && inst.loader === 'vanilla';
  list.innerHTML = res.items.length ? res.items.map((h) => `
    <div class="item" data-id="${esc(h.id)}">
      ${st.type === 'modpack' ? '' : `<label class="check ${h.installed ? 'installed' : ''}" title="${h.installed ? INSTALLED_TIP : 'Выбрать для пакетной установки'}"><input type="checkbox" data-pick ${st.picked.has(h.id) || (h.installed && h.enabled) ? 'checked' : ''}><span class="box">${iconSvg('check')}</span></label>`}
      ${modIcon(h)}
      <div class="grow"><div class="row"><span class="title">${esc(h.title)}</span><span class="chip">${esc(CAT_TYPES.find((t) => t[0] === st.type)[1].toLowerCase())}</span></div>
        <div class="desc">${esc(h.description)}</div><div class="small faint" style="margin-top:3px">автор: ${esc(h.author)}</div></div>
      <div class="stats"><span>${ic('download')} ${fmtNum(h.downloads)}</span><span>${ic('heart')} ${fmtNum(h.follows)}</span>
        ${h.installed && st.type !== 'modpack' ? installedChip(h.enabled)
          : `<button class="btn sm primary" data-install ${needFabric ? 'disabled title="Нужна сборка с модами"' : ''}>${ic('download')} ${st.type === 'modpack' ? 'Создать сборку' : 'Установить'}</button>`}
        <button class="btn sm ghost" data-open title="Страница проекта">${ic('link')}</button></div>
    </div>`).join('')
    : `<div class="empty"><b>Ничего не найдено</b><span>Попробуйте другой запрос${inst ? ` или проверьте, есть ли проекты под ${esc(inst.game_version)}` : ''}</span></div>`;
  if (/opti\s*fine/i.test(st.query)) list.insertAdjacentHTML('afterbegin', OPTIFINE_CARD(inst));
  $('#catPager', el).innerHTML = `<span class="small">Страница</span><input class="input" id="pageInput" value="${st.page + 1}">
    <span class="small muted grow">из ${fmtNum(st.pages)}</span>
    <button class="btn sm" data-p="-1" ${st.page ? '' : 'disabled'}>Назад</button><button class="btn sm" data-p="1" ${st.page + 1 < st.pages ? '' : 'disabled'}>Вперёд</button>`;
  hydrateIcons(el);
  if (needFabric) toast('Моды ставятся в сборки с Fabric, Forge или NeoForge — выберите другую сборку', 'warn');

  $('#catPager', el).onclick = (e) => { const p = e.target.dataset.p; if (p) { st.page += +p; loadCatalog(el); $('.page').scrollTo({ top: 0, behavior: 'smooth' }); } };
  $('#pageInput', el).onchange = (e) => { st.page = Math.max(0, Math.min(st.pages - 1, (+e.target.value || 1) - 1)); loadCatalog(el); };
  list.onchange = (e) => {
    if (!e.target.matches('[data-pick]')) return;
    const id = e.target.closest('[data-id]').dataset.id;
    const hit = st.items.find((h) => h.id === id);
    if (hit.installed) {                    // галочка у установленного = включён ли он в сборке
      setHitEnabled(el, hit, e.target.checked, e.target);
      return;
    }
    e.target.checked ? st.picked.set(id, hit.title) : st.picked.delete(id);
    renderBatch(el);
  };
  list.onclick = async (e) => {
    const row = e.target.closest('[data-id]'); if (!row) return;
    const hit = st.items.find((h) => h.id === row.dataset.id);
    if (e.target.closest('[data-open]')) api.open_url(hit.url);
    if (e.target.closest('[data-uninstall]')) return uninstallHit(el, hit);
    const btn = e.target.closest('[data-install]');
    if (btn) await installHit(el, hit, btn);
  };
  renderBatch(el);
}

async function installHit(el, hit, btn) {
  const st = el.__st;
  const original = btn ? btn.innerHTML : '';
  // после ошибки кнопка возвращается к обычному виду — пользователь мог просто передумать
  const restore = () => { if (btn && btn.isConnected) { btn.disabled = false; btn.innerHTML = original; } };
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>'; }
  const res = await safe(api.catalog_install(st.source, st.type, hit.id, hit.title, st.instance));
  if (!res) { restore(); return false; }
  if (res.error) {
    if (res.url) {
      // автор запретил скачивание из лаунчеров — кнопка ведёт на страницу, где файл можно взять вручную
      if (btn && btn.isConnected) {
        btn.disabled = false; btn.removeAttribute('data-install'); btn.classList.remove('primary');
        btn.innerHTML = `${ic('link')} На сайте`; btn.title = 'Автор разрешил скачивать только с сайта';
        btn.onclick = (ev) => { ev.stopPropagation(); api.open_url(res.url); };
        hydrateIcons(btn);
      }
      if (await confirmDialog('Скачайте вручную', res.error, 'Открыть страницу')) api.open_url(res.url);
    } else { restore(); toast(res.error, 'error', 6000); }
    return false;
  }
  if (st.type === 'modpack') { toast(`Создаю сборку «${hit.title}» — прогресс внизу`); return true; }
  const extra = res.files.length > 1 ? ` (+${res.files.length - 1} завис.)` : '';
  const row = $(`.item[data-id="${CSS.escape(hit.id)}"]`, el);
  if (btn) { btn.outerHTML = installedChip(true); if (row) hydrateIcons(row); }
  hit.installed = true; hit.enabled = true;
  const cb = row && $('[data-pick]', row);
  if (cb) { cb.checked = true; cb.closest('.check').classList.add('installed'); cb.closest('.check').title = INSTALLED_TIP; }
  st.picked.delete(hit.id); renderBatch(el);
  toast(res.files.length ? `${hit.title} установлен${extra}` : `${hit.title} уже установлен`);
  return true;
}

// OptiFine нет ни на Modrinth, ни на CurseForge: автор разрешает скачивать его только с optifine.net
const OPTIFINE_CARD = (inst) => `<div class="key-card optifine-card">
  <b>${ic('alert')} OptiFine — только с официального сайта</b>
  <span>Автор OptiFine запрещает выкладывать его в каталоги и раздавать через лаунчеры, поэтому здесь его нет.
    Скачайте файл на <b>optifine.net</b> (версия должна совпадать с игрой${inst ? ` — ${esc(inst.game_version)}` : ''}),
    затем в сборке на вкладке «Моды» нажмите «Файл .jar». Нужна сборка с Forge (на Fabric OptiFine без дополнительного мода OptiFabric не работает).
    <br><b>Важно:</b> OptiFine несовместим с Embeddium/Sodium, ModernFix и Oculus/Iris — с ними игра вылетит.
    Альтернатива без конфликтов: Embeddium + Oculus (Forge) или Sodium + Iris (Fabric) — они есть в каталоге.</span>
  <div class="row"><button class="btn sm primary" onclick="api.open_url('https://optifine.net/downloads')">${ic('link')} Открыть optifine.net</button></div></div>`;

const INSTALLED_TIP = 'Галочка — используется в сборке. Снимите, чтобы выключить (файл останется)';

// «установлено» / «выключено»; при наведении превращается в «удалить»
const installedChip = (enabled) => `<button class="chip ${enabled ? 'accent' : 'off'} installed-chip" data-uninstall title="Удалить из сборки">
  <span class="on-idle">${enabled ? `${ic('check')} установлено` : `${ic('minus')} выключено`}</span><span class="on-hover">${ic('trash')} удалить</span></button>`;

async function setHitEnabled(el, hit, enabled, checkbox) {
  const st = el.__st;
  checkbox.disabled = true;
  const res = await safe(api.catalog_set_enabled(st.source, hit.id, st.instance, enabled));
  checkbox.disabled = false;
  if (!res || res.error) {
    checkbox.checked = hit.enabled;
    if (res) toast(res.error, 'error');
    return;
  }
  hit.enabled = res.enabled;
  checkbox.checked = res.enabled;
  const row = checkbox.closest('[data-id]');
  const chip = row && $('.installed-chip', row);
  if (chip) { chip.outerHTML = installedChip(res.enabled); hydrateIcons(row); }
  toast(res.enabled ? `«${hit.title}» снова включён` : `«${hit.title}» выключен — остаётся в сборке, но игра его не загружает`);
}

async function uninstallHit(el, hit) {
  const st = el.__st;
  const kind = { mod: 'мод', resourcepack: 'ресурспак', shader: 'шейдер' }[st.type] || 'файл';
  const inst = S.instances.find((i) => i.id === st.instance);
  if (!await confirmDialog(`Удалить ${kind}?`, `«${hit.title}» будет удалён из сборки${inst ? ` «${inst.name}»` : ''}.`, 'Удалить', true)) return;
  const res = await safe(api.catalog_uninstall(st.source, hit.id, st.instance));
  if (!res) return;
  if (res.error) return toast(res.error, 'error');
  hit.installed = false;
  toast(`«${hit.title}» удалён`);
  loadCatalog(el);
}

function renderBatch(el) {
  const st = el.__st, box = $('#batch', el);
  if (!box) return;
  if (!st.picked.size) { box.innerHTML = ''; return; }
  box.innerHTML = `<div class="batch"><b>Выбрано: ${st.picked.size}</b><span class="small muted grow">${esc([...st.picked.values()].slice(0, 4).join(', '))}${st.picked.size > 4 ? '…' : ''}</span>
    <button class="btn sm ghost" data-b="clear">Сбросить</button><button class="btn sm primary" data-b="go">${ic('download')} Скачать пачкой</button></div>`;
  hydrateIcons(box);
  box.onclick = async (e) => {
    const b = e.target.closest('[data-b]')?.dataset.b;
    if (b === 'clear') { st.picked.clear(); loadCatalog(el); }
    if (b === 'go') {
      const items = [...st.picked.entries()];
      st.picked.clear(); renderBatch(el);
      toast(`Устанавливаю ${items.length} шт....`);
      let ok = 0;
      for (const [id, title] of items) { if (await installHit(el, { id, title }, null)) ok++; }
      toast(`Готово: установлено ${ok} из ${items.length}`);
      loadCatalog(el);
    }
  };
}

function pickCatalogInstance(el) {
  const usable = S.instances;
  if (!usable.length) return openNewInstance();
  const m = openModal(`<h3>Куда устанавливать?</h3><p class="muted small">Каталог покажет проекты для версии и загрузчика выбранной сборки.</p>
    <div class="inst-grid" style="grid-template-columns:1fr 1fr">${usable.map((i) => instCardHtml(i, i.id === el.__st.instance)).join('')}</div>`, { wide: true });
  bindInstCards(m);
  $$('.inst-card', m).forEach((c) => c.addEventListener('click', () => {
    el.__st.instance = c.dataset.id; el.__st.page = 0; m.close(); PAGES.catalog.refresh(el); hydrateIcons(el);
  }));
}
