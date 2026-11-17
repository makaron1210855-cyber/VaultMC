/* =============================================================================
   Пиксельная графика на <canvas>: скин игрока (вид спереди), голова-аватар,
   иконки сборок (генерируются из названия). Всё рисуется без размытия.
   ============================================================================= */

const skinCache = {};  // dataURL -> Promise<Image>

function loadImage(src) {
  if (!skinCache[src]) {
    skinCache[src] = new Promise((resolve) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    });
  }
  return skinCache[src];
}

function hash(str) { // FNV-1a
  let h = 2166136261;
  for (const c of String(str)) { h ^= c.codePointAt(0); h = Math.imul(h, 16777619); }
  return h >>> 0;
}
function rng(seed) { return () => ((seed = Math.imul(seed ^ (seed >>> 15), 2246822507) + 0x9e3779b9 >>> 0) / 4294967296); }

function hexToRgb(hex) { const n = parseInt(hex.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255]; }
function shade(hex, k) {
  const [r, g, b] = hexToRgb(hex);
  const f = (v) => Math.max(0, Math.min(255, Math.round(k > 0 ? v + (255 - v) * k : v * (1 + k))));
  return `rgb(${f(r)},${f(g)},${f(b)})`;
}

/* ---------------------------------------------------------------- иконка сборки */
function drawInstanceIcon(canvas, name, color = '#22c55e', icon = '') {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  if (icon) {  // своя картинка сборки
    canvas.width = 128; canvas.height = 128; canvas.classList.add('photo');
    const img = new Image();
    img.onload = () => { ctx.clearRect(0, 0, 128, 128); ctx.drawImage(img, 0, 0, 128, 128); };
    img.src = icon;
    return;
  }
  canvas.classList.remove('photo');
  canvas.width = 16; canvas.height = 16;
  const r = rng(hash(name));
  ctx.fillStyle = shade(color, -0.72); ctx.fillRect(0, 0, 16, 16);
  // мягкий фон-градиент из «блоков»
  for (let y = 0; y < 16; y += 2) for (let x = 0; x < 16; x += 2) {
    if (r() < 0.18) { ctx.fillStyle = shade(color, -0.6 + r() * 0.1); ctx.fillRect(x, y, 2, 2); }
  }
  // симметричное «существо» 8x8 по центру
  const body = shade(color, 0), dark = shade(color, -0.35), light = shade(color, 0.35);
  for (let y = 0; y < 8; y++) for (let x = 0; x < 4; x++) {
    const v = r();
    const solid = (y >= 1 && y <= 6 && x >= 1) || v < 0.45;
    if (!solid) continue;
    ctx.fillStyle = v < 0.2 ? light : (v > 0.85 ? dark : body);
    ctx.fillRect(4 + x, 4 + y, 1, 1); ctx.fillRect(11 - x, 4 + y, 1, 1);
  }
  ctx.fillStyle = '#ffffff'; ctx.fillRect(6, 7, 1, 1); ctx.fillRect(9, 7, 1, 1);  // глаза
  ctx.fillStyle = '#0b0b10'; ctx.fillRect(6, 8, 1, 1); ctx.fillRect(9, 8, 1, 1);
}

/* ---------------------------------------------------------------- свой персонаж по умолчанию (не Стив) */
function drawDefaultFigure(ctx, accent) {
  const A = accent, A2 = shade(accent, -0.45), D = '#1b1d28', D2 = '#12131b', SK = '#d9a37a', G = '#2a2d3a';
  const px = (x, y, w, h, c) => { ctx.fillStyle = c; ctx.fillRect(x, y, w, h); };
  // голова: капюшон + неоновый визор
  px(4, 0, 8, 8, D); px(5, 2, 6, 5, SK); px(5, 3, 6, 2, D2); px(5, 3, 6, 1, A); px(6, 4, 4, 1, A2);
  px(4, 0, 8, 1, D2); px(7, 6, 2, 1, '#b07d5a');
  // тело: куртка с неоновыми полосами
  px(4, 8, 8, 12, D); px(7, 8, 2, 12, D2); px(4, 8, 1, 12, A2); px(11, 8, 1, 12, A2); px(5, 13, 6, 1, A);
  // руки
  px(0, 8, 4, 12, D); px(12, 8, 4, 12, D); px(0, 17, 4, 1, A); px(12, 17, 4, 1, A); px(0, 18, 4, 2, SK); px(12, 18, 4, 2, SK);
  // ноги
  px(4, 20, 4, 12, G); px(8, 20, 4, 12, shade('#2a2d3a', -0.15)); px(4, 30, 8, 2, D2); px(4, 29, 8, 1, A2);
}

/* ---------------------------------------------------------------- скин: вид спереди 16x32 */
async function drawSkin(canvas, skinUrl, accent) {
  const ctx = canvas.getContext('2d');
  const img = skinUrl ? await loadImage(skinUrl) : null;
  if (!img) {
    canvas.width = 16; canvas.height = 32;
    ctx.imageSmoothingEnabled = false;
    drawDefaultFigure(ctx, accent);
    return;
  }
  const info = skinInfo(img);
  const k = info.k;                                     // 1 для 64×64, 2 для HD 128×128 и т.д.
  canvas.width = 16 * k; canvas.height = 32 * k;
  ctx.imageSmoothingEnabled = false;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  const part = (sx, sy, w, h, dx, dy, flip = false) => {
    const args = [img, sx * k, sy * k, w * k, h * k];
    if (!flip) return ctx.drawImage(...args, dx * k, dy * k, w * k, h * k);
    ctx.save(); ctx.translate((dx + w) * k, dy * k); ctx.scale(-1, 1); ctx.drawImage(...args, 0, 0, w * k, h * k); ctx.restore();
  };
  const aw = info.slim ? 3 : 4;
  part(8, 8, 8, 8, 4, 0);                              // голова
  part(20, 20, 8, 12, 4, 8);                           // тело
  part(44, 20, aw, 12, 4 - aw, 8);                     // правая рука (слева на экране)
  if (info.modern) part(36, 52, aw, 12, 12, 8); else part(44, 20, aw, 12, 12, 8, true);
  part(4, 20, 4, 12, 4, 20);                           // правая нога
  if (info.modern) part(20, 52, 4, 12, 8, 20); else part(4, 20, 4, 12, 8, 20, true);
  // второй слой (шапка, куртка...)
  if (info.hat) part(40, 8, 8, 8, 4, 0);
  if (info.modern) {
    part(20, 36, 8, 12, 4, 8); part(44, 36, aw, 12, 4 - aw, 8); part(52, 52, aw, 12, 12, 8);
    part(4, 36, 4, 12, 4, 20); part(4, 52, 4, 12, 8, 20);
  }
}

/** Разбор скина: масштаб (HD), формат (64×64 или старый 64×32), тонкие руки, рисовать ли шапку. */
function skinInfo(img) {
  const k = Math.max(1, Math.round(img.width / 64));
  const modern = img.height >= img.width;              // 64×64 (и HD-квадраты); 64×32 — старый формат
  let slim = false, hat = true;
  try {
    const c = document.createElement('canvas'); c.width = img.width; c.height = img.height;
    const g = c.getContext('2d'); g.drawImage(img, 0, 0);
    const alphaIn = (x, y, w, h) => { const d = g.getImageData(x * k, y * k, w * k, h * k).data; const out = [];
      for (let i = 3; i < d.length; i += 4) out.push(d[i]); return out; };
    // тонкие руки: у слим-скина столбцы 54–55 на руке (12 пикселей в высоту) полностью прозрачные
    if (modern) slim = alphaIn(54, 20, 2, 12).every((a) => a === 0);
    // как в самой игре: если у старого скина слой шапки совсем без прозрачности — это заливка, а не шапка
    if (!modern) hat = alphaIn(32, 0, 32, 16).some((a) => a < 128);
  } catch (e) { /* картинка с другого сайта без доступа к пикселям — рисуем как есть */ }
  return { k, modern, slim, hat };
}

/* ---------------------------------------------------------------- голова-аватар 8x8 */
async function drawHead(canvas, skinUrl, accent) {
  const ctx = canvas.getContext('2d');
  const img = skinUrl ? await loadImage(skinUrl) : null;
  if (!img) {
    canvas.width = 8; canvas.height = 8;
    ctx.imageSmoothingEnabled = false;
    const tmp = document.createElement('canvas'); tmp.width = 16; tmp.height = 32;
    drawDefaultFigure(tmp.getContext('2d'), accent);
    ctx.drawImage(tmp, 4, 0, 8, 8, 0, 0, 8, 8);
    return;
  }
  const { k, hat } = skinInfo(img);
  canvas.width = 8 * k; canvas.height = 8 * k;
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(img, 8 * k, 8 * k, 8 * k, 8 * k, 0, 0, 8 * k, 8 * k);
  if (hat) ctx.drawImage(img, 40 * k, 8 * k, 8 * k, 8 * k, 0, 0, 8 * k, 8 * k);
}

/** Скин аккаунта: берём из кэша или спрашиваем у Python (Ely.by / Microsoft / свой файл).
 *  Запрос один на аккаунт: кто спросил одновременно — ждёт тот же ответ (раньше второй получал пустоту). */
const SKIN_REQUESTS = {};
async function skinFor(accountId) {
  if (!accountId) return '';
  if (S.skins[accountId] !== undefined) return S.skins[accountId];
  if (!SKIN_REQUESTS[accountId]) {
    SKIN_REQUESTS[accountId] = safe(api.get_skin(accountId)).then((url) => {
      S.skins[accountId] = url || '';
      delete SKIN_REQUESTS[accountId];
      return S.skins[accountId];
    });
  }
  return SKIN_REQUESTS[accountId];
}

/* ---------------------------------------------------------------- живой персонаж на блоке травы */
// Собираем фигуру из частей (голова, тело, руки, ноги), чтобы их можно было анимировать по отдельности.
async function drawSkinRig(container, skinUrl, accent) {
  const flat = document.createElement('canvas');
  await drawSkin(flat, skinUrl, accent);            // вид спереди 16×32 со вторым слоем и слим-руками
  const parts = {                                    // [x, y, w, h] на развёртке 16×32
    legR: [4, 20, 4, 12], legL: [8, 20, 4, 12],
    armR: [0, 8, 4, 12], body: [4, 8, 8, 12], armL: [12, 8, 4, 12], head: [4, 0, 8, 8],
  };
  const rig = document.createElement('div');
  rig.className = 'rig';
  const upper = document.createElement('div');
  upper.className = 'rig-upper';
  const k = flat.width / 16;                          // HD-скин: в каждой «клетке» k×k пикселей
  for (const [name, [x, y, w, h]] of Object.entries(parts)) {
    const c = document.createElement('canvas');
    c.width = w * k; c.height = h * k; c.className = 'rig-part ' + name;
    c.style.cssText = `left:calc(${x} * var(--u));top:calc(${y} * var(--u));width:calc(${w} * var(--u));height:calc(${h} * var(--u))`;
    c.getContext('2d').drawImage(flat, x * k, y * k, w * k, h * k, 0, 0, w * k, h * k);
    (name.startsWith('leg') ? rig : upper).append(c);
  }
  rig.append(upper);
  // блок травы под ногами и тень
  const ground = document.createElement('canvas');
  ground.className = 'rig-ground'; ground.width = 24; ground.height = 8;
  const g = ground.getContext('2d'), r = rng(7);
  for (let yy = 0; yy < 8; yy++) for (let xx = 0; xx < 24; xx++) {
    let col;
    if (yy < 2 || (yy === 2 && r() < 0.5)) col = ['#5fae3c', '#4f9a32', '#6cbf44'][Math.floor(r() * 3)];
    else col = ['#7a5434', '#6b4a2e', '#86603d', '#5e4128'][Math.floor(r() * 4)];
    g.fillStyle = col; g.fillRect(xx, yy, 1, 1);
  }
  g.fillStyle = 'rgba(0,0,0,.28)'; g.fillRect(7, 0, 10, 1); g.fillRect(8, 1, 8, 1);   // тень от ног
  const shadow = document.createElement('div'); shadow.className = 'rig-shadow';
  container.innerHTML = '';
  container.append(shadow, ground, rig);
  rigLookAt(container, null);
  // клик: персонаж подпрыгивает и «достаёт из хранилища» добычу — алмаз, золото или изумруд
  rig.onclick = () => {
    if (rig.classList.contains('hop')) return;      // пока идёт прыжок, повторные клики не нужны
    rig.classList.add('hop');
    container.classList.add('hopping');
    setTimeout(() => spawnLoot(container), 230);     // добыча появляется, когда рука уже поднята
    setTimeout(() => { rig.classList.remove('hop'); container.classList.remove('hopping'); }, 950);
  };
}

// Добыча вылетает из поднятой руки: вращается, светится, вокруг разлетаются искры.
function spawnLoot(container) {
  if (!container.isConnected) return;
  const kind = ['diamond', 'gold', 'emerald'][Math.floor(Math.random() * 3)];
  const wrap = document.createElement('div');
  wrap.className = 'rig-loot ' + kind;
  const glow = document.createElement('i'); glow.className = 'glow';
  const c = document.createElement('canvas'); c.width = 8; c.height = 8;
  drawLoot(c.getContext('2d'), kind);
  wrap.append(glow, c);
  for (let i = 0; i < 9; i++) {
    const a = (i / 9) * Math.PI * 2 + Math.random() * 0.5, d = 4 + Math.random() * 5;
    const s = document.createElement('span'); s.className = 'rig-spark';
    s.style.cssText = `--dx:${(Math.cos(a) * d).toFixed(1)};--dy:${(Math.sin(a) * d).toFixed(1)};--d:${(Math.random() * 0.25 + 0.25).toFixed(2)}s`;
    wrap.append(s);
  }
  container.append(wrap);
  setTimeout(() => wrap.remove(), 2000);
}

// пиксельная добыча 8×8: алмаз, слиток золота или изумруд
function drawLoot(g, kind) {
  const C = {
    diamond: ['#1aa3b0', '#4ae6ef', '#b8fbff'], gold: ['#b07a12', '#f2c037', '#fff3a8'], emerald: ['#0e7a3b', '#2fd06a', '#b9ffd0'],
  }[kind];
  const shape = kind === 'gold'
    ? ['........', '........', '..1111..', '.122221.', '12233221', '11111111', '........', '........']
    : ['...11...', '..1221..', '.122321.', '12233221', '.122221.', '..1221..', '...11...', '........'];
  shape.forEach((row, y) => [...row].forEach((ch, x) => {
    if (ch === '.') return;
    g.fillStyle = C[+ch - 1]; g.fillRect(x, y, 1, 1);
  }));
}

// Голова персонажа плавно смотрит в сторону курсора (поворот «в объёме», без сдвига с шеи).
// Курсор ушёл или давно не двигался — голова так же плавно возвращается и спокойно оглядывается сама.
const RIG_LOOK = new WeakMap();

function rigLookAt(container, clientX, clientY) {
  if (container && container.__lh) return container.__lh.look(clientX, clientY);   // 3D-персонаж главной (lobby3d.js)
  const head = container?.querySelector('.rig-part.head');
  if (!head) return;
  let st = RIG_LOOK.get(head);
  if (!st) {
    st = { x: 0, y: 0, tx: 0, ty: 0, last: 0, t0: performance.now(), raf: 0 };
    RIG_LOOK.set(head, st);
  }
  if (clientX === null || clientX === undefined) {
    st.last = 0;                                   // курсор ушёл — пусть смотрит сам
  } else {
    const r = head.getBoundingClientRect();
    const dx = (clientX - (r.left + r.width / 2)) / 420;
    const dy = (clientY - (r.top + r.height / 2)) / 420;
    st.tx = Math.max(-1, Math.min(1, dx));
    st.ty = Math.max(-1, Math.min(1, dy));
    st.last = performance.now();
  }
  if (!st.raf) st.raf = requestAnimationFrame((t) => rigLookStep(head, st, t));
}

function rigLookStep(head, st, now) {
  st.raf = 0;
  if (!head.isConnected) return;
  if (document.body.classList.contains('ui-idle')) return;   // лаунчер в простое — кадры не рисуем (uiWake продолжит)
  if (!document.hidden) {
    let tx = st.tx, ty = st.ty;
    if (!st.last || now - st.last > 2500) {         // без курсора: медленно и негромко оглядывается
      const k = (now - st.t0) / 1000;
      tx = Math.sin(k * 0.45) * 0.45 + Math.sin(k * 0.17) * 0.2;
      ty = Math.sin(k * 0.3 + 1.3) * 0.18;
    }
    const ease = 0.075;                            // доля пути за кадр — мягко, без рывков
    st.x += (tx - st.x) * ease;
    st.y += (ty - st.y) * ease;
    head.style.transform = `perspective(calc(60 * var(--u))) rotateY(${(st.x * 22).toFixed(2)}deg) `
      + `rotateX(${(-st.y * 12).toFixed(2)}deg) rotate(${(st.x * 2).toFixed(2)}deg)`;
    const wrap = head.closest('.skin-wrap');          // всё тело и блок травы чуть поворачиваются вслед за головой
    if (wrap) wrap.style.setProperty('--ry', (st.x * 10).toFixed(2) + 'deg');
  }
  st.raf = requestAnimationFrame((t) => rigLookStep(head, st, t));
}
