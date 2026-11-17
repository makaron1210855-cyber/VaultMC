/* =============================================================================
   Общие помощники интерфейса: состояние, навигация с анимацией, окна, меню, уведомления.
   ============================================================================= */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtNum = (n) => Number(n || 0).toLocaleString('ru-RU');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Глобальное состояние, которое приходит из Python (bootstrap) и обновляется событиями. */
const S = {
  config: {}, account: null, accounts: [], instances: [], state: 'idle', progress: [0, 'Готово к запуску'],
  skins: {}, page: 'lobby', roomAddress: '',
  get selected() { return this.instances.find((i) => i.id === this.config.selected_instance) || this.instances[0] || null; },
};

function html(strings) { const t = document.createElement('template'); t.innerHTML = strings.trim(); return t.content.firstElementChild; }

/* ---------------------------------------------------------------- навигация */
const PAGES = {};   // имя -> { render(el, params), onShow?(el), title? }
let currentPageEl = null;

function registerPage(name, def) { PAGES[name] = def; }

const PAGE_TITLES = { lobby: '', instances: 'Сборки', instance: 'Сборка', catalog: 'Моды', skins: 'Скины', rooms: 'Комнаты',
  friends: 'Друзья', settings: 'Настройки', logs: 'Консоль' };

function go(name, params = {}) {
  const def = PAGES[name];
  if (!def) return;
  S.page = name;
  S.pageParams = params;
  if (!params.noSave && ['lobby', 'instances', 'catalog', 'skins', 'rooms', 'friends', 'settings', 'logs'].includes(name)) {
    api.save_ui('page', name).catch(() => {});
  }
  $$('#nav button, .nav-bottom button').forEach((b) => b.classList.toggle('active', b.dataset.page === name || (name === 'instance' && b.dataset.page === 'instances')));
  moveTabInk();
  const tbPage = $('#tbPage');
  if (tbPage) tbPage.textContent = PAGE_TITLES[name] || '';
  const container = $('#pages');
  const old = currentPageEl;
  const el = document.createElement('div');
  el.className = 'page';
  el.dataset.page = name;
  el.classList.add('fresh');                    // блоки «всплывают» только при открытии вкладки
  setTimeout(() => el.classList.remove('fresh'), 900);
  def.render(el, params);
  hydrateIcons(el);
  container.appendChild(el);
  currentPageEl = el;
  if (old) { old.classList.add('leaving'); setTimeout(() => old.remove(), 170); }
  renderPlaybar();
}

/** Подложка активной вкладки в верхней полосе плавно переезжает под нужную кнопку. */
function moveTabInk() {
  const nav = $('#nav'), ink = $('#tabInk');
  if (!nav || !ink) return;
  const btn = $('button.active', nav);
  nav.classList.toggle('inked', !!btn);
  if (!btn) return;
  ink.style.width = btn.offsetWidth + 'px';
  ink.style.transform = `translateX(${btn.offsetLeft}px)`;
}
window.addEventListener('resize', () => moveTabInk());

/** Перерисовать текущую страницу без анимации перехода (после изменения данных). */
function refreshPage() {
  if (!currentPageEl) return;
  const def = PAGES[S.page];
  if (def && def.refresh) { def.refresh(currentPageEl); hydrateIcons(currentPageEl); }
}

/** Полностью перезагрузить текущую страницу: заново запрашивает данные (друзья, статусы, моды). */
async function reloadPage() {
  if (!S.page) return;
  const btn = $('#tbReload');
  if (btn) { btn.classList.add('spinning'); setTimeout(() => btn.classList.remove('spinning'), 600); }
  if (['instances', 'lobby', 'instance'].includes(S.page)) await reloadInstances().catch(() => {});
  const keep = S.page === 'instance' && currentPageEl && currentPageEl.__tab ? { tab: currentPageEl.__tab } : {};   // та же вкладка сборки
  go(S.page, { ...(S.pageParams || {}), ...keep, noSave: true });
}

/* ---------------------------------------------------------------- уведомления */
function toast(text, kind = 'info', ms = 3800) {
  const t = html(`<div class="toast ${kind}" style="--ms:${ms}ms"><span class="dot"></span><div>${esc(text)}</div></div>`);
  $('#toasts').appendChild(t);
  setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 260); }, ms);
}
/** Ответ Python вида {error: "..."} -> уведомление; возвращает true, если ошибки нет. */
function okOrToast(res) {
  if (res && res.error) { toast(res.error, 'error', 6000); return false; }
  return true;
}
async function safe(promise) {
  try { return await promise; } catch (e) { toast(e.message || String(e), 'error', 6000); return null; }
}

/* ---------------------------------------------------------------- модальные окна */
function openModal(contentHtml, { wide = false, onClose } = {}) {
  const root = $('#modalRoot');
  root.innerHTML = '';
  const back = html('<div class="backdrop"></div>');
  const modal = html(`<div class="modal ${wide ? 'wide' : ''}"><button class="btn ghost sm icon x" data-close>${ic('x')}</button>${contentHtml}</div>`);
  root.append(back, modal);
  root.classList.add('open');
  const close = () => { root.classList.remove('open'); root.innerHTML = ''; onClose && onClose(); };
  back.onclick = close;
  modal.querySelectorAll('[data-close]').forEach((b) => (b.onclick = close));
  hydrateIcons(modal);
  modal.close = close;
  return modal;
}
function closeModal() { const r = $('#modalRoot'); r.classList.remove('open'); r.innerHTML = ''; }

function confirmDialog(title, text, okText = 'Да', danger = false) {
  return new Promise((resolve) => {
    const m = openModal(`<h3>${esc(title)}</h3><p class="muted">${esc(text)}</p>
      <div class="foot"><button class="btn ghost" data-no>Отмена</button><button class="btn ${danger ? 'danger' : 'primary'}" data-yes>${esc(okText)}</button></div>`,
    { onClose: () => resolve(false) });
    m.querySelector('[data-no]').onclick = () => { closeModal(); resolve(false); };
    m.querySelector('[data-yes]').onclick = () => { closeModal(); resolve(true); };
  });
}

/* ---------------------------------------------------------------- выпадающие меню */
function openDropdown(anchor, contentHtml, align = 'right') {
  const root = $('#dropdownRoot');
  root.innerHTML = '';
  const dd = html(`<div class="dropdown">${contentHtml}</div>`);
  root.appendChild(dd);
  root.classList.add('open');
  const r = anchor.getBoundingClientRect();
  dd.style.top = r.bottom + 8 + 'px';
  if (align === 'right') dd.style.right = Math.max(8, window.innerWidth - r.right) + 'px';
  else { dd.style.left = r.left + 'px'; dd.style.transformOrigin = 'top left'; }
  root.onclick = (e) => { if (e.target === root) closeDropdown(); };
  hydrateIcons(dd);
  return dd;
}
function closeDropdown() { const r = $('#dropdownRoot'); r.classList.remove('open'); r.innerHTML = ''; }

/* ---------------------------------------------------------------- маленькие компоненты */
const checkHtml = (key, label, on) =>
  `<label class="check"><input type="checkbox" data-key="${key}" ${on ? 'checked' : ''}><span class="box">${iconSvg('check')}</span>${esc(label)}</label>`;
const switchHtml = (key, title, sub, on) =>
  `<label class="switch"><input type="checkbox" data-key="${key}" ${on ? 'checked' : ''}><span class="track"></span><span class="lbl"><b>${esc(title)}</b>${sub ? `<small>${esc(sub)}</small>` : ''}</span></label>`;
const segHtml = (name, items, value, accent = false) =>
  `<div class="seg ${accent ? 'accent' : ''}" data-seg="${name}">${items.map(([v, l]) => `<button data-v="${v}" class="${v === value ? 'on' : ''}">${esc(l)}</button>`).join('')}</div>`;

/** Подключает переключение к сегментам .seg[data-seg] внутри root. */
function bindSegs(root, onChange) {
  $$('.seg[data-seg]', root).forEach((seg) => seg.addEventListener('click', (e) => {
    const b = e.target.closest('button[data-v]');
    if (!b || b.classList.contains('on')) return;
    $$('button', seg).forEach((x) => x.classList.toggle('on', x === b));
    syncSeg(seg);
    onChange(seg.dataset.seg, b.dataset.v);
  }));
}

/* ---------------------------------------------------------------- «живые» элементы дизайна */
/** Подложка у переключателя .seg переезжает под выбранную кнопку (вместо резкой смены цвета). */
function syncSeg(seg) {
  const on = $('button.on', seg);
  let ink = $(':scope > .seg-ink', seg);
  if (!on) { if (ink) ink.style.opacity = '0'; return; }
  if (!seg.offsetWidth) return;                       // ещё не показан — подвинем, когда появится
  if (!ink) { ink = document.createElement('i'); ink.className = 'seg-ink'; seg.prepend(ink); seg.classList.add('inked'); ink.style.transition = 'none'; }
  ink.style.opacity = '1';
  ink.style.width = on.offsetWidth + 'px';
  ink.style.transform = `translateX(${on.offsetLeft - 4}px)`;
  if (ink.style.transition === 'none') requestAnimationFrame(() => { ink.style.transition = ''; });
}
function syncSegs(root = document) { $$('.seg', root).forEach(syncSeg); }
// новые переключатели появляются при каждой отрисовке — ловим их сами
new MutationObserver((list) => {
  for (const m of list) for (const n of m.addedNodes) {
    if (n.nodeType !== 1) continue;
    if (n.matches('.seg')) syncSeg(n); else if (n.querySelector('.seg')) syncSegs(n);
  }
  // выбранную кнопку могли поменять кодом (без клика) — сверяем
  for (const m of list) { const seg = m.target.closest && m.target.closest('.seg'); if (seg && m.type === 'attributes') syncSeg(seg); }
}).observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
window.addEventListener('resize', () => syncSegs());

// «волна» от нажатия на любую кнопку
document.addEventListener('pointerdown', (e) => {
  const b = e.target.closest('.btn');
  if (!b || b.disabled) return;
  const r = b.getBoundingClientRect(), d = Math.max(r.width, r.height) * 2.2;
  const w = document.createElement('span');
  w.className = 'ripple';
  w.style.cssText = `width:${d}px;height:${d}px;left:${e.clientX - r.left - d / 2}px;top:${e.clientY - r.top - d / 2}px`;
  b.appendChild(w);
  setTimeout(() => w.remove(), 650);
}, { passive: true });

// «прожектор»: карточка под курсором подсвечивается там, где мышь
let spotEl = null, spotRaf = 0, spotEv = null;
document.addEventListener('pointermove', (e) => {
  spotEv = e;
  if (spotRaf) return;
  spotRaf = requestAnimationFrame(() => {
    spotRaf = 0;
    const el = spotEv.target.closest && spotEv.target.closest('.card, .plate, .fr-join');
    if (spotEl && spotEl !== el) spotEl.classList.remove('spot-hover');
    spotEl = el;
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty('--mx', `${spotEv.clientX - r.left}px`);
    el.style.setProperty('--my', `${spotEv.clientY - r.top}px`);
    el.classList.add('spot-hover');
  });
}, { passive: true });

const TYPE_CHIP = { release: 'accent', snapshot: 'warn', old_beta: 'info', old_alpha: 'pink', fabric: 'sand', forge: 'warn', neoforge: 'info', local: '' };
// Вход через Microsoft пока выключен (нужен свой Client ID с одобрением Mojang). Включить: true
const MS_LOGIN_ENABLED = false;
const KIND_CHIP = { offline: '', elyby: 'info', microsoft: 'accent' };

function copyText(text) {
  const done = () => toast('Скопировано');
  if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => fallback());
  else fallback();
  function fallback() {
    const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove(); done();
  }
}

function debounce(fn, ms = 250) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
