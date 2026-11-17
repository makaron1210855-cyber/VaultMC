/* =============================================================================
   Мастерская скинов (страница «Скины»).
   Редактор по пикселям (на схеме скина и прямо на персонаже), плащи, аксессуары
   (корона, очки, наушники…), свой каталог скинов, загрузка с Ely.by по нику,
   по ссылке и из файла. Сохранение отправляет скин и плащ на сервер VaultMC —
   их видят все, кто играет через VaultMC в сборке с модами.
   ============================================================================= */

/* ---------------------------------------------------------------- развёртка скина (UV) */
// [x, y, w, h] на картинке 64×64 для каждой грани; второй слой («одежда») — свои координаты
const UV = {
  head: { front: [8, 8, 8, 8], back: [24, 8, 8, 8], o: { front: [40, 8, 8, 8], back: [56, 8, 8, 8] } },
  body: { front: [20, 20, 8, 12], back: [32, 20, 8, 12], o: { front: [20, 36, 8, 12], back: [32, 36, 8, 12] } },
  rarm: { front: [44, 20, 4, 12], back: [52, 20, 4, 12], o: { front: [44, 36, 4, 12], back: [52, 36, 4, 12] } },
  larm: { front: [36, 52, 4, 12], back: [44, 52, 4, 12], o: { front: [52, 52, 4, 12], back: [60, 52, 4, 12] } },
  rleg: { front: [4, 20, 4, 12], back: [12, 20, 4, 12], o: { front: [4, 36, 4, 12], back: [12, 36, 4, 12] } },
  lleg: { front: [20, 52, 4, 12], back: [28, 52, 4, 12], o: { front: [4, 52, 4, 12], back: [12, 52, 4, 12] } },
};
// где часть стоит на «виде персонажа» 16×32 (спереди; сзади руки и ноги меняются местами)
const LAYOUT = { head: [4, 0], body: [4, 8], rarm: [0, 8], larm: [12, 8], rleg: [4, 20], lleg: [8, 20] };
const BACK_SWAP = { rarm: 'larm', larm: 'rarm', rleg: 'lleg', lleg: 'rleg' };
// области второго слоя на схеме — подсвечиваем, когда выбран слой «одежда»
const OVERLAY_ZONES = [[32, 0, 32, 16], [0, 32, 56, 16], [0, 48, 16, 16], [48, 48, 16, 16]];

const PALETTE = ['#1b1b1f', '#3a3a44', '#6b6b78', '#b8b8c4', '#f4f4f6', '#5b3a29', '#8a5a3c', '#c99a6e', '#f2c8a0',
  '#7a1f2b', '#d0342c', '#f07a2a', '#f6c84a', '#3f8f3a', '#2bb673', '#1f6f8b', '#2e86de', '#5b4bd6', '#a24bd6', '#e85fa8'];

const CAPE_PRESETS = [
  { id: 'solid', name: 'Однотонный' }, { id: 'split', name: 'Две половины' }, { id: 'stripes', name: 'Полосы' },
  { id: 'gradient', name: 'Градиент' }, { id: 'diamond', name: 'Ромб' }, { id: 'cross', name: 'Крест' },
  { id: 'vault', name: 'Хранилище' }, { id: 'stars', name: 'Звёзды' },
];

const GEAR = [
  { id: 'crown', name: 'Корона' }, { id: 'glasses', name: 'Очки' }, { id: 'shades', name: 'Тёмные очки' },
  { id: 'headphones', name: 'Наушники' }, { id: 'cap', name: 'Кепка' }, { id: 'mask', name: 'Маска' },
  { id: 'bandana', name: 'Бандана' }, { id: 'scarf', name: 'Шарф' }, { id: 'backpack', name: 'Рюкзак' },
  { id: 'helmet', name: 'Шлем' }, { id: 'hood', name: 'Капюшон' }, { id: 'apron', name: 'Фартук' },
];

/* ---------------------------------------------------------------- состояние */
const SK = {
  canvas: null, ctx: null,           // скин 64×64
  cape: null, capeCtx: null,         // плащ 64×32
  slim: false, layer: 'base', tool: 'pencil', color: '#2e86de', recent: [],
  undo: [], redo: [], dirty: false, tab: 'editor', hasCape: false, view: '3d', viewer: null,
};

function skCanvas(w, h) { const c = document.createElement('canvas'); c.width = w; c.height = h; return c; }

function loadImg(url) {
  return new Promise((resolve, reject) => { const i = new Image(); i.onload = () => resolve(i); i.onerror = reject; i.src = url; });
}

/* ---------------------------------------------------------------- проверка: это правда скин? */
// коробки развёртки Minecraft: [u, v, ширина, высота, глубина]; всё вне них — пустые зоны
const SKIN_BOXES = [[0, 0, 8, 8, 8], [32, 0, 8, 8, 8], [16, 16, 8, 12, 4], [40, 16, 4, 12, 4], [0, 16, 4, 12, 4],
  [16, 32, 8, 12, 4], [40, 32, 4, 12, 4], [0, 32, 4, 12, 4], [16, 48, 4, 12, 4], [0, 48, 4, 12, 4], [32, 48, 4, 12, 4], [48, 48, 4, 12, 4]];

/** Пустая строка — похоже на скин; иначе — понятная причина, почему файл не подходит. */
async function checkSkin(url) {
  let img;
  try { img = await loadImg(url); } catch (e) { return 'Не удалось открыть картинку'; }
  const w = img.naturalWidth, h = img.naturalHeight;
  if (w < 64 || w % 64 || (h !== w && h !== w / 2)) return `Картинка ${w}×${h} — не скин. Нужен скин Minecraft 64×64 (или 64×32, HD 128×128)`;
  const H = h === w ? 64 : 32;
  const c = skCanvas(64, H), x = c.getContext('2d');
  x.imageSmoothingEnabled = false; x.drawImage(img, 0, 0, 64, H);
  const px = x.getImageData(0, 0, 64, H).data;
  const alpha = (u, v) => px[(v * 64 + u) * 4 + 3];
  const used = new Uint8Array(64 * H);
  for (const [u, v, bw, bh, d] of SKIN_BOXES) {
    if (v + d + bh > H) continue;
    const mark = (rx, ry, rw, rh) => { for (let j = ry; j < ry + rh; j++) for (let i = rx; i < rx + rw; i++) used[j * 64 + i] = 1; };
    mark(u + d, v, 2 * bw, d); mark(u, v + d, 2 * d + 2 * bw, bh);
  }
  const filled = (rx, ry, rw, rh) => { let n = 0; for (let j = ry; j < ry + rh; j++) for (let i = rx; i < rx + rw; i++) if (alpha(i, j) > 200) n++; return n / (rw * rh); };
  if (filled(8, 8, 8, 8) < 0.85 || filled(20, 20, 8, 12) < 0.85) return 'Это не скин: у скина лицо и тело всегда закрашены, а здесь на их месте пусто';
  let free = 0, opaque = 0; const colors = new Set();
  for (let j = 0; j < H; j++) for (let i = 0; i < 64; i++) {
    if (used[j * 64 + i]) continue;
    free++;
    const k = (j * 64 + i) * 4;
    if (px[k + 3] > 200) { opaque++; colors.add((px[k] >> 4) << 8 | (px[k + 1] >> 4) << 4 | (px[k + 2] >> 4)); }
  }
  if (opaque / free > 0.9 && colors.size > 24) return 'Это похоже на обычную картинку, а не на скин: у скина развёртка — пустые места между частями тела';
  return '';
}

/** Тонкие руки (модель Alex): у таких скинов крайние столбцы рук пустые (или залиты сплошь чёрным/белым). */
function detectSlim(canvas) {
  const px = canvas.getContext('2d').getImageData(0, 0, 64, 64).data;
  const zones = [[50, 16, 2, 4], [54, 20, 2, 12], [42, 48, 2, 4], [46, 52, 2, 12]];
  const all = (test) => zones.every(([zx, zy, w, h]) => {
    for (let j = zy; j < zy + h; j++) for (let i = zx; i < zx + w; i++) if (!test((j * 64 + i) * 4)) return false;
    return true;
  });
  const clear = (k) => px[k + 3] === 0;
  const black = (k) => px[k + 3] === 255 && px[k] === 0 && px[k + 1] === 0 && px[k + 2] === 0;
  const white = (k) => px[k + 3] === 255 && px[k] === 255 && px[k + 1] === 255 && px[k + 2] === 255;
  return all(clear) || all(black) || all(white);
}

/** У скина нарисованы все 4 столбца рук (обычная модель Steve)? */
function hasClassicArms(canvas) {
  if (detectSlim(canvas)) return false;
  const px = canvas.getContext('2d').getImageData(0, 0, 64, 64).data;
  return [[54, 20, 2, 12], [46, 52, 2, 12]].every(([zx, zy, w, h]) => {
    for (let j = zy; j < zy + h; j++) for (let i = zx; i < zx + w; i++) if (px[(j * 64 + i) * 4 + 3] < 200) return false;
    return true;
  });
}

function setSlim(v) {
  SK.slim = !!v;
  const cb = SK.root && $('#skSlim', SK.root); if (cb) cb.checked = SK.slim;
}

/** Любой скин (64×32, 64×64, HD) → наш холст 64×64 (старый формат достраивается как в игре). */
async function skinToCanvas(url) {
  const img = await loadImg(url);
  const c = skCanvas(64, 64), x = c.getContext('2d');
  x.imageSmoothingEnabled = false;
  if (img.height * 2 === img.width) {               // 64×32: левые рука и нога = зеркало правых
    x.drawImage(img, 0, 0, img.width, img.height, 0, 0, 64, 32);
    const mirror = (sx, sy, dx, dy) => { x.save(); x.translate(dx + 16, dy); x.scale(-1, 1); x.drawImage(c, sx, sy, 16, 16, 0, 0, 16, 16); x.restore(); };
    mirror(0, 16, 16, 48); mirror(40, 16, 32, 48);
  } else {
    x.drawImage(img, 0, 0, img.width, img.height, 0, 0, 64, 64);
  }
  return c;
}

function snapshot() { return SK.ctx.getImageData(0, 0, 64, 64); }
function pushUndo() { SK.undo.push(snapshot()); if (SK.undo.length > 60) SK.undo.shift(); SK.redo = []; SK.dirty = true; }

/* ---------------------------------------------------------------- рисование персонажа */
function partRect(part, side, layer) {
  const p = UV[part];
  const r = layer === 'over' ? p.o[side] : p[side];
  if (SK.slim && (part === 'rarm' || part === 'larm')) {
    // тонкие руки: ширина 3, у задней грани сдвиг на 1 влево
    return side === 'back' ? [r[0] - 1, r[1], 3, r[3]] : [r[0], r[1], 3, r[3]];
  }
  return r;
}

/** Вид персонажа (спереди или сзади) в масштабе s: рисуем грани из скина. */
function drawView(target, side, s, showCape) {
  const x = target.getContext('2d');
  x.imageSmoothingEnabled = false;
  x.clearRect(0, 0, target.width, target.height);
  const ox = 2 * s, oy = 2 * s;                 // поля под плащ/второй слой
  for (const layer of ['base', 'over']) {
    for (const part of Object.keys(UV)) {
      const place = side === 'back' ? LAYOUT[BACK_SWAP[part] || part] : LAYOUT[part];
      const [sx, sy, w, h] = partRect(part, side, layer);
      let dx = place[0];
      if (SK.slim && part === (side === 'back' ? 'larm' : 'rarm')) dx += 1;
      x.drawImage(SK.canvas, sx, sy, w, h, ox + dx * s, oy + place[1] * s, w * s, h * s);
    }
  }
  if (side === 'back' && showCape && SK.hasCape) {   // плащ висит на спине поверх одежды
    x.drawImage(SK.cape, 1, 1, 10, 16, ox + 3 * s, oy + 8 * s, 10 * s, 16 * s);
  }
}

/** Точка на виде персонажа → пиксель скина (с учётом выбранного слоя). */
function viewToUV(side, vx, vy) {
  for (const part of Object.keys(UV)) {
    const place = side === 'back' ? LAYOUT[BACK_SWAP[part] || part] : LAYOUT[part];
    const [sx, sy, w, h] = partRect(part, side, SK.layer === 'over' ? 'over' : 'base');
    let px = place[0];
    if (SK.slim && part === (side === 'back' ? 'larm' : 'rarm')) px += 1;
    if (vx >= px && vx < px + w && vy >= place[1] && vy < place[1] + h) return [sx + (vx - px), sy + (vy - place[1])];
  }
  return null;
}

function isOverlay(u, v) { return OVERLAY_ZONES.some(([x, y, w, h]) => u >= x && u < x + w && v >= y && v < y + h); }

/* ---------------------------------------------------------------- инструменты */
function hexToRgba(hex) { const n = parseInt(hex.slice(1), 16); return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 255]; }
function rgbaToHex(d) { return '#' + [d[0], d[1], d[2]].map((v) => v.toString(16).padStart(2, '0')).join(''); }

function paintAt(ctx, w, h, u, v, allowClear) {
  if (u < 0 || v < 0 || u >= w || v >= h) return;
  if (SK.tool === 'picker') {
    const d = ctx.getImageData(u, v, 1, 1).data;
    if (d[3]) setColor(rgbaToHex(d));
    return;
  }
  if (SK.tool === 'eraser') {
    // основа скина не может быть прозрачной — там ластик красит в цвет кожи по умолчанию
    if (allowClear(u, v)) ctx.clearRect(u, v, 1, 1);
    else { ctx.fillStyle = '#c99a6e'; ctx.fillRect(u, v, 1, 1); }
    return;
  }
  if (SK.tool === 'fill') return floodFill(ctx, w, h, u, v, hexToRgba(SK.color));
  ctx.fillStyle = SK.color;
  ctx.fillRect(u, v, 1, 1);
}

function floodFill(ctx, w, h, x0, y0, rgba) {
  const img = ctx.getImageData(0, 0, w, h), d = img.data;
  const at = (x, y) => (y * w + x) * 4;
  const start = d.slice(at(x0, y0), at(x0, y0) + 4);
  if (start.every((v, i) => v === rgba[i])) return;
  const same = (i) => d[i] === start[0] && d[i + 1] === start[1] && d[i + 2] === start[2] && d[i + 3] === start[3];
  // заливаем только внутри той же грани — иначе краска «утечёт» на соседние части развёртки
  const face = faceOf(x0, y0, w, h);
  const stack = [[x0, y0]];
  while (stack.length) {
    const [x, y] = stack.pop();
    if (x < face[0] || y < face[1] || x >= face[0] + face[2] || y >= face[1] + face[3]) continue;
    const i = at(x, y);
    if (!same(i)) continue;
    d[i] = rgba[0]; d[i + 1] = rgba[1]; d[i + 2] = rgba[2]; d[i + 3] = rgba[3];
    stack.push([x + 1, y], [x - 1, y], [x, y + 1], [x, y - 1]);
  }
  ctx.putImageData(img, 0, 0);
}

function faceOf(u, v, w, h) {
  if (w === 64 && h === 32) return u < 22 && v < 17 ? (u < 11 ? [0, 0, 11, 17] : [11, 0, 11, 17]) : [0, 0, 64, 32];
  for (const p of Object.values(UV)) {
    for (const r of [p.front, p.back, p.o.front, p.o.back]) {
      if (u >= r[0] && u < r[0] + r[2] && v >= r[1] && v < r[1] + r[3]) return r;
    }
  }
  return [Math.floor(u / 8) * 8, Math.floor(v / 8) * 8, 8, 8];
}

function setColor(hex) {
  SK.color = hex;
  SK.recent = [hex, ...SK.recent.filter((c) => c !== hex)].slice(0, 8);
  const root = SK.root && $('#skStudio', SK.root); if (!root) return;
  $('#skColor', root).value = hex;
  $$('.sk-swatch', root).forEach((b) => b.classList.toggle('on', b.dataset.c === hex));
  const rec = $('#skRecent', root);
  if (rec) rec.innerHTML = SK.recent.map((c) => `<button class="sk-swatch" data-c="${c}" style="--c:${c}"></button>`).join('');
}

/* ---------------------------------------------------------------- готовые скины (свои, оригинальные) */
const TONES = ['#f2c8a0', '#e0a97c', '#c68a5d', '#9a6440', '#6e452b', '#f7d9bf'];
const HAIRS = ['#2a1d16', '#5b3a29', '#b07a3b', '#e3c46e', '#1b1b1f', '#c2452d', '#9aa0a8', '#6a3fb5'];
const OUTFITS = [
  ['Шахтёр', '#4a5d7a', '#2d3442', '#3a2a1e', 'helmet'], ['Лесник', '#3f8f3a', '#5b3a29', '#2a1d16', 'none'],
  ['Кузнец', '#7a1f2b', '#3a3a44', '#1b1b1f', 'apron'], ['Исследователь', '#c99a6e', '#5b4a35', '#3a2a1e', 'scarf'],
  ['Механик', '#f07a2a', '#2d3442', '#1b1b1f', 'none'], ['Маг', '#5b4bd6', '#2a2050', '#1b1b1f', 'hood'],
  ['Рыцарь', '#9aa0a8', '#6b6b78', '#3a3a44', 'helmet'], ['Пират', '#d0342c', '#2a1d16', '#1b1b1f', 'bandana'],
  ['Учёный', '#f4f4f6', '#3a3a44', '#1b1b1f', 'glasses'], ['Фермер', '#f6c84a', '#1f6f8b', '#5b3a29', 'cap'],
  ['Ниндзя', '#1b1b1f', '#1b1b1f', '#1b1b1f', 'mask'], ['Повар', '#f4f4f6', '#6b6b78', '#1b1b1f', 'none'],
  ['Скейтер', '#2bb673', '#2e86de', '#f4f4f6', 'cap'], ['Пилот', '#5b3a29', '#3a3a44', '#2a1d16', 'glasses'],
  ['Геймер', '#a24bd6', '#1b1b1f', '#f4f4f6', 'headphones'], ['Строитель', '#f6c84a', '#1f6f8b', '#3a2a1e', 'helmet'],
  ['Космонавт', '#f4f4f6', '#b8b8c4', '#6b6b78', 'none'], ['Ковбой', '#8a5a3c', '#1f6f8b', '#5b3a29', 'cap'],
  ['Хакер', '#2d3442', '#1b1b1f', '#2bb673', 'shades'], ['Дракон', '#3f8f3a', '#2a5d2a', '#1b1b1f', 'none'],
  ['Снеговик', '#e8f4ff', '#2e86de', '#d0342c', 'scarf'], ['Неон', '#e85fa8', '#2e86de', '#f4f4f6', 'shades'],
  ['Охотник', '#5b4a35', '#3f8f3a', '#2a1d16', 'hood'], ['Король', '#d0342c', '#f6c84a', '#1b1b1f', 'crown'],
];

function rect(x, c, a, b, w, h) { x.fillStyle = c; x.fillRect(a, b, w, h); }
function skShade(hex, k) { const [r, g, b] = hexToRgba(hex); const f = (v) => Math.max(0, Math.min(255, Math.round(v * k))); return '#' + [f(r), f(g), f(b)].map((v) => v.toString(16).padStart(2, '0')).join(''); }

/** Рисует оригинальный скин по описанию — так каталог не зависит от чужих работ. */
function makeCatalogSkin(n) {
  const [name, shirt, pants, shoes, extra] = OUTFITS[n % OUTFITS.length];
  const tone = TONES[n % TONES.length], hair = HAIRS[(n * 3) % HAIRS.length];
  const c = skCanvas(64, 64), x = c.getContext('2d');
  // кожа: голова, руки, ноги
  for (const p of Object.values(UV)) for (const r of [p.front, p.back]) rect(x, tone, ...r);
  for (const [a, b] of [[0, 8], [16, 8], [8, 0], [16, 0]]) rect(x, tone, a, b, 8, 8);
  for (const [a, b, w] of [[40, 20, 4], [48, 20, 4], [32, 52, 4], [40, 52, 4], [0, 20, 4], [8, 20, 4], [16, 52, 4], [24, 52, 4]]) rect(x, tone, a, b, w, 12);
  rect(x, tone, 44, 16, 8, 4); rect(x, tone, 36, 48, 8, 4); rect(x, tone, 4, 16, 8, 4); rect(x, tone, 20, 48, 8, 4);
  // волосы: макушка, затылок, виски, чёлка
  rect(x, hair, 8, 0, 8, 8); rect(x, hair, 24, 8, 8, 8); rect(x, hair, 0, 8, 8, 4); rect(x, hair, 16, 8, 8, 4);
  rect(x, hair, 8, 8, 8, 2); rect(x, skShade(hair, 1.25), 9, 8, 3, 1);
  // лицо
  rect(x, '#f4f4f6', 9, 12, 2, 1); rect(x, '#f4f4f6', 13, 12, 2, 1);
  rect(x, '#2e3b5a', 10, 12, 1, 1); rect(x, '#2e3b5a', 13, 12, 1, 1);
  rect(x, skShade(tone, 0.82), 11, 13, 2, 1); rect(x, skShade(tone, 0.6), 11, 14, 2, 1);
  // одежда: тело и верх рук
  for (const r of [[20, 20, 8, 12], [32, 20, 8, 12], [16, 20, 4, 12], [28, 20, 4, 12], [20, 16, 8, 4], [28, 16, 8, 4]]) rect(x, shirt, ...r);
  for (const [a, b] of [[40, 20], [44, 20], [48, 20], [52, 20], [32, 52], [36, 52], [40, 52], [44, 52]]) rect(x, shirt, a, b, 4, 5);
  rect(x, skShade(shirt, 0.75), 20, 31, 8, 1); rect(x, skShade(shirt, 1.18), 23, 20, 2, 12);
  // штаны и обувь
  for (const [a, b] of [[0, 20], [4, 20], [8, 20], [12, 20], [16, 52], [20, 52], [24, 52], [28, 52]]) { rect(x, pants, a, b, 4, 9); rect(x, shoes, a, b + 9, 4, 3); }
  rect(x, skShade(pants, 0.7), 20, 30, 8, 2);
  // «изюминка» во втором слое
  if (extra && extra !== 'none') applyGear(x, extra, skShade(shirt, 0.85));
  return { name, url: c.toDataURL('image/png') };
}

/* ---------------------------------------------------------------- аксессуары: рисуются во втором слое */
function applyGear(x, id, color) {
  const c = color || SK.color;
  const dark = skShade(c, 0.6), light = skShade(c, 1.3);
  const hat = (cb) => cb();    // для читаемости
  switch (id) {
    case 'crown':
      hat(() => {
        for (const [fx, fy] of [[40, 8], [56, 8], [32, 8], [48, 8]]) { rect(x, '#f6c84a', fx, fy, 8, 2); }
        for (const fx of [40, 43, 46, 56, 59, 62, 32, 35, 38, 48, 51, 54]) rect(x, '#f6c84a', fx, 7, 2, 1);
        rect(x, '#d0342c', 43, 8, 2, 1); rect(x, '#2e86de', 59, 8, 2, 1);
      }); break;
    case 'glasses':
      rect(x, '#1b1b1f', 40, 12, 8, 1); rect(x, 'rgba(180,220,255,.75)', 41, 12, 2, 1); rect(x, 'rgba(180,220,255,.75)', 45, 12, 2, 1);
      rect(x, '#1b1b1f', 32, 12, 8, 1); rect(x, '#1b1b1f', 48, 12, 8, 1); break;
    case 'shades':
      rect(x, '#111', 40, 11, 8, 2); rect(x, '#3a3a44', 41, 11, 2, 1); rect(x, '#3a3a44', 45, 11, 2, 1);
      rect(x, '#111', 32, 12, 8, 1); rect(x, '#111', 48, 12, 8, 1); break;
    case 'headphones':
      rect(x, c, 32, 0, 0, 0);
      rect(x, dark, 40, 0, 8, 1); rect(x, dark, 32, 8, 8, 1); rect(x, dark, 48, 8, 8, 1);
      rect(x, c, 33, 11, 3, 4); rect(x, c, 51, 11, 3, 4); rect(x, light, 34, 12, 1, 2); rect(x, light, 52, 12, 1, 2); break;
    case 'cap':
      rect(x, c, 40, 0, 8, 8); rect(x, c, 40, 8, 8, 2); rect(x, c, 56, 8, 8, 2); rect(x, c, 32, 8, 8, 2); rect(x, c, 48, 8, 8, 2);
      rect(x, dark, 40, 10, 8, 1); rect(x, light, 43, 8, 2, 1); break;
    case 'mask':
      rect(x, c, 40, 13, 8, 3); rect(x, c, 32, 13, 8, 3); rect(x, c, 48, 13, 8, 3); rect(x, c, 56, 13, 8, 3); rect(x, dark, 40, 13, 8, 1); break;
    case 'bandana':
      rect(x, c, 40, 8, 8, 2); rect(x, c, 32, 8, 8, 2); rect(x, c, 48, 8, 8, 2); rect(x, c, 56, 8, 8, 2);
      rect(x, '#f4f4f6', 41, 8, 1, 1); rect(x, '#f4f4f6', 45, 9, 1, 1); rect(x, c, 60, 10, 2, 3); break;
    case 'scarf':
      rect(x, c, 20, 36, 8, 2); rect(x, c, 32, 36, 8, 2); rect(x, c, 16, 36, 4, 2); rect(x, c, 28, 36, 4, 2);
      rect(x, dark, 21, 38, 2, 4); rect(x, light, 20, 37, 8, 1); break;
    case 'backpack':
      rect(x, c, 33, 37, 6, 9); rect(x, dark, 33, 37, 6, 1); rect(x, light, 34, 40, 4, 2); rect(x, dark, 21, 36, 1, 7); rect(x, dark, 26, 36, 1, 7); break;
    case 'helmet':
      rect(x, '#9aa0a8', 40, 0, 8, 8); for (const fx of [32, 40, 48, 56]) rect(x, '#9aa0a8', fx, 8, 8, 3);
      rect(x, '#f6c84a', 43, 8, 2, 2); break;
    case 'hood':
      rect(x, c, 40, 0, 8, 8); rect(x, c, 32, 8, 8, 8); rect(x, c, 48, 8, 8, 8); rect(x, c, 56, 8, 8, 8); rect(x, c, 40, 8, 8, 1);
      rect(x, c, 40, 9, 1, 7); rect(x, c, 47, 9, 1, 7); rect(x, dark, 40, 0, 8, 1); break;
    case 'apron':
      rect(x, '#5b3a29', 21, 38, 6, 10); rect(x, '#3a2a1e', 21, 38, 6, 1); break;
    default: break;
  }
}

/* ---------------------------------------------------------------- плащи */
function makeCape(id, a, b) {
  const c = skCanvas(64, 32), x = c.getContext('2d');
  const face = (fn) => { fn(1, 1); fn(12, 1); };       // внешняя и внутренняя стороны
  rect(x, skShade(a, 0.7), 0, 0, 22, 17);                 // края
  face((ox, oy) => {
    switch (id) {
      case 'split': rect(x, a, ox, oy, 5, 16); rect(x, b, ox + 5, oy, 5, 16); break;
      case 'stripes': for (let i = 0; i < 16; i++) rect(x, i % 4 < 2 ? a : b, ox, oy + i, 10, 1); break;
      case 'gradient': for (let i = 0; i < 16; i++) { const t = i / 15; rect(x, mix(a, b, t), ox, oy + i, 10, 1); } break;
      case 'diamond': rect(x, a, ox, oy, 10, 16); for (let i = 0; i < 5; i++) rect(x, b, ox + 4 - i, oy + 3 + i, 2 + i * 2, 1), rect(x, b, ox + 4 - i, oy + 12 - i, 2 + i * 2, 1); break;
      case 'cross': rect(x, a, ox, oy, 10, 16); rect(x, b, ox + 4, oy + 2, 2, 12); rect(x, b, ox + 1, oy + 5, 8, 2); break;
      case 'vault':
        rect(x, a, ox, oy, 10, 16); rect(x, b, ox + 2, oy + 4, 6, 7); rect(x, a, ox + 3, oy + 5, 4, 5);
        rect(x, b, ox + 4, oy + 6, 2, 3); rect(x, skShade(b, 1.3), ox + 2, oy + 4, 6, 1); break;
      case 'stars':
        rect(x, a, ox, oy, 10, 16);
        for (const [sx, sy] of [[2, 2], [7, 4], [4, 7], [8, 10], [1, 12], [5, 14]]) rect(x, b, ox + sx, oy + sy, 1, 1);
        break;
      default: rect(x, a, ox, oy, 10, 16);
    }
  });
  rect(x, skShade(a, 0.8), 22, 0, 24, 22);                // элитры — цветом плаща
  return c;
}

function mix(a, b, t) {
  const A = hexToRgba(a), B = hexToRgba(b);
  return '#' + [0, 1, 2].map((i) => Math.round(A[i] + (B[i] - A[i]) * t).toString(16).padStart(2, '0')).join('');
}

/* ---------------------------------------------------------------- страница */
registerPage('skins', {
  async render(el) {
    el.innerHTML = '<div class="row muted"><span class="spinner"></span> Открываю мастерскую...</div>';
    const st = await safe(api.skin_studio());
    if (!st || !el.isConnected) return;
    SK.info = st;
    SK.canvas = skCanvas(64, 64); SK.ctx = SK.canvas.getContext('2d');
    SK.cape = skCanvas(64, 32); SK.capeCtx = SK.cape.getContext('2d');
    SK.undo = []; SK.redo = []; SK.dirty = false; SK.slim = !!st.slim; SK.hasCape = !!st.cape;
    try { if (st.skin) SK.ctx.drawImage(await skinToCanvas(st.skin), 0, 0); else SK.ctx.drawImage(skCanvas(64, 64), 0, 0); } catch (e) { /* пустой холст */ }
    if (!st.skin) { const s = makeCatalogSkin(0); SK.ctx.drawImage(await loadImg(s.url), 0, 0); }
    if (st.skin && !SK.slim && detectSlim(SK.canvas)) SK.slim = true;   // скин с тонкими руками, сохранённый как обычный
    if (st.cape) SK.capeCtx.drawImage(await loadImg(st.cape), 0, 0, 64, 32);
    this.refresh(el);
  },

  refresh(el) {
    SK.root = el;
    const st = SK.info;
    if (!st.account) {
      el.innerHTML = '<div class="empty"><b>Войдите в аккаунт</b><span>Скины привязаны к аккаунту.</span></div>';
      return;
    }
    const editable = st.editable;
    const SK_TABS = [['editor', 'brush', 'Редактор', 'Рисуйте пикселями'], ['cape', 'shirt', 'Плащ', 'Узоры и цвета'],
      ['gear', 'star', 'Аксессуары', 'Шапки, очки, маски'], ['catalog', 'image', 'Каталог', 'Готовые скины']];
    el.innerHTML = `<div class="sk-wrap">
      <div class="sk-head">
        <div class="h1">Мастерская скинов</div>
        <p class="muted small">${editable
          ? (st.server ? `${ic('globe')} Скин и плащ увидят все, кто играет через VaultMC в сборке с модами — и по сети, и на серверах.`
            : 'Сервер скинов не настроен — скин будет виден только вам.')
          : `${ic('alert')} У аккаунта ${esc(st.account.kindLabel)} скин меняется на сайте сервиса. Рисовать можно с оффлайн-аккаунтом.`}</p>
      </div>
      <div class="sk-grid" id="skStudio">
        <div class="sk-stage plate">
          <div class="row" style="justify-content:center">${segHtml('skview', [['3d', '3D'], ['2d', 'Рисовать на персонаже']], SK.view)}</div>
          <div class="sk-3d" id="sk3d" ${SK.view === '3d' ? '' : 'hidden'}></div>
          <div class="sk-views" ${SK.view === '2d' ? '' : 'hidden'}>
            <div><canvas id="skFront"></canvas><small>спереди</small></div>
            <div><canvas id="skBack"></canvas><small>сзади</small></div>
          </div>
          <div class="sk-save" style="flex-wrap:wrap">
            <label class="check"><input type="checkbox" id="skSlim" ${SK.slim ? 'checked' : ''}><span class="box">${iconSvg('check')}</span></label>
            <span class="small" style="white-space:nowrap" title="Руки шириной 3 пикселя, как у Алекс">Тонкие руки</span><div class="grow"></div>
            <button class="btn" data-a="export" title="Сохранить скин в файл">${ic('download')}</button>
            <div class="sk-twin" style="flex-basis:100%;display:grid;grid-template-columns:1fr 1fr;gap:8px">
              <button class="btn" data-a="upload" title="Скин Minecraft .png 64×64, 64×32 или HD" ${editable ? '' : 'disabled'}>${ic('upload')} Загрузить PNG</button>
              <button class="btn ghost" data-a="reset" title="Вернуть стандартный скин" ${editable && st.skin ? '' : 'disabled'}>${ic('undo')} Сбросить</button>
            </div>
            ${editable && !st.skin ? '<div class="small faint" style="flex-basis:100%">Сейчас у вас стандартный скин. На персонаже — заготовка: нарисуйте своё или нажмите «Сохранить», чтобы поставить её.</div>' : ''}
            <button class="btn primary" data-a="save" style="flex-basis:100%;justify-content:center" ${editable ? '' : 'disabled'}>${ic('check')} Сохранить${st.server ? ' и показать всем' : ''}</button>
          </div>
        </div>
        <div class="sk-panel plate" id="skPanel">
          <nav class="sk-tabs" id="skTabs"><i class="sk-tabs-ink"></i>${SK_TABS.map(([id, icon, name, sub]) => `
            <button data-sktab="${id}" class="${SK.tab === id ? 'on' : ''}">${ic(icon)}<span><b>${name}</b><small>${sub}</small></span></button>`).join('')}</nav>
          <div class="sk-body" id="skBody"></div>
        </div>
      </div></div>`;
    hydrateIcons(el);
    // вкладки мастерской: подложка едет под выбранную, содержимое сразу меняется рядом (без прокрутки вниз)
    const tabs = $('#skTabs', el);
    const moveInk = () => {
      const on = $('button.on', tabs), ink = $('.sk-tabs-ink', tabs);
      if (!on || !on.offsetWidth) return;
      ink.style.width = on.offsetWidth + 'px'; ink.style.transform = `translateX(${on.offsetLeft}px)`; ink.style.opacity = '1';
    };
    requestAnimationFrame(moveInk);
    new ResizeObserver(moveInk).observe(tabs);
    tabs.onclick = (e) => {
      const b = e.target.closest('[data-sktab]');
      if (!b || b.dataset.sktab === SK.tab) return;
      $$('button', tabs).forEach((x) => x.classList.toggle('on', x === b));
      moveInk();
      SK.tab = b.dataset.sktab;
      renderPanel(el);
    };
    bindSegs(el, (name, v) => {
      if (name === 'skview') {
        SK.view = v;
        $('#sk3d', el).hidden = v !== '3d'; $('.sk-views', el).hidden = v !== '2d';
        redraw();
      }
    });
    if (SK.viewer) SK.viewer.destroy();
    SK.viewer = createSkinViewer($('#sk3d', el), {});
    $('#skSlim', el).onchange = (e) => { SK.slim = e.target.checked; SK.dirty = true; redraw(); };
    el.onclick = (e) => {
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'save') saveStudio();
      if (a === 'export') exportSkin();
      if (a === 'reset') resetSkin(el);
      if (a === 'upload') importSkinFile();
    };
    bindPaint($('#skFront', el), 'front');
    bindPaint($('#skBack', el), 'back');
    renderPanel(el);
    redraw();
  },
});

let viewer3dT = 0;
function redraw() {
  if (SK.viewer && SK.view === '3d') {                // 3D обновляем не чаще 4 раз в секунду — пересборка граней не бесплатная
    clearTimeout(viewer3dT);
    viewer3dT = setTimeout(() => SK.viewer && SK.viewer.update({ skin: SK.canvas, slim: SK.slim, cape: SK.hasCape ? SK.cape : null }), 250);
    if (!SK.viewer.ready) { SK.viewer.ready = true; clearTimeout(viewer3dT); SK.viewer.update({ skin: SK.canvas, slim: SK.slim, cape: SK.hasCape ? SK.cape : null }); }
  }
  const s = 9;
  for (const [id, side] of [['skFront', 'front'], ['skBack', 'back']]) {
    const cv = SK.root && $('#' + id, SK.root); if (!cv) continue;
    cv.width = 20 * s; cv.height = 36 * s;
    drawView(cv, side, s, true);
  }
  const uv = SK.root && $('#skUV', SK.root);
  if (uv) drawUV(uv);
  const cv = SK.root && $('#skCapeUV', SK.root);
  if (cv) drawCapeUV(cv);
}

/* рисование мышкой — на персонаже и на схеме */
function strokeHandler(target, toUV, ctxGet, w, h, allowClear) {
  let down = false;
  const apply = (e) => {
    const r = target.getBoundingClientRect();
    const uv = toUV((e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height);
    if (!uv) return;
    paintAt(ctxGet(), w, h, uv[0], uv[1], allowClear);
    if (ctxGet() === SK.capeCtx) SK.hasCape = true;
    redraw();
  };
  target.onpointerdown = (e) => {
    if (!SK.info.editable) return toast('Редактировать можно скин оффлайн-аккаунта', 'warn');
    if (SK.tool !== 'picker') pushUndo();
    down = SK.tool === 'pencil' || SK.tool === 'eraser';
    target.setPointerCapture(e.pointerId);
    apply(e);
  };
  target.onpointermove = (e) => { if (down) apply(e); };
  target.onpointerup = () => { down = false; };
  target.onpointercancel = () => { down = false; };
}

function bindPaint(cv, side) {
  strokeHandler(cv, (fx, fy) => {
    const vx = Math.floor(fx * 20) - 2, vy = Math.floor(fy * 36) - 2;
    return viewToUV(side, vx, vy);
  }, () => SK.ctx, 64, 64, (u, v) => isOverlay(u, v));
}

/* схема скина (развёртка) */
function drawUV(cv) {
  const s = 7; cv.width = 64 * s; cv.height = 64 * s;
  const x = cv.getContext('2d'); x.imageSmoothingEnabled = false;
  // шахматка — видно прозрачные места
  for (let i = 0; i < 64; i++) for (let j = 0; j < 64; j++) { x.fillStyle = (i + j) % 2 ? '#20232b' : '#262a33'; x.fillRect(i * s, j * s, s, s); }
  x.drawImage(SK.canvas, 0, 0, 64, 64, 0, 0, 64 * s, 64 * s);
  x.fillStyle = SK.layer === 'over' ? 'rgba(0,0,0,.45)' : 'rgba(0,0,0,0)';
  if (SK.layer === 'over') {                  // слой «одежда»: затемняем основу
    x.fillRect(0, 0, 64 * s, 64 * s);
    for (const [a, b, w, h] of OVERLAY_ZONES) x.drawImage(SK.canvas, a, b, w, h, a * s, b * s, w * s, h * s);
  }
  x.strokeStyle = 'rgba(255,255,255,.05)';
  for (let i = 0; i <= 64; i += 1) { x.beginPath(); x.moveTo(i * s + 0.5, 0); x.lineTo(i * s + 0.5, 64 * s); x.stroke(); x.beginPath(); x.moveTo(0, i * s + 0.5); x.lineTo(64 * s, i * s + 0.5); x.stroke(); }
}

function drawCapeUV(cv) {
  const s = 12; cv.width = 22 * s; cv.height = 17 * s;
  const x = cv.getContext('2d'); x.imageSmoothingEnabled = false;
  for (let i = 0; i < 22; i++) for (let j = 0; j < 17; j++) { x.fillStyle = (i + j) % 2 ? '#20232b' : '#262a33'; x.fillRect(i * s, j * s, s, s); }
  x.drawImage(SK.cape, 0, 0, 22, 17, 0, 0, 22 * s, 17 * s);
  x.strokeStyle = 'rgba(255,255,255,.35)'; x.strokeRect(1 * s + 0.5, 1 * s + 0.5, 10 * s, 16 * s);
}

function toolsHtml() {
  const tools = [['pencil', 'brush', 'Кисть'], ['eraser', 'eraser', 'Ластик'], ['fill', 'bucket', 'Заливка'], ['picker', 'pipette', 'Пипетка']];
  return `<div class="sk-tools">${tools.map(([id, icon, name]) => `<button class="sk-tool ${SK.tool === id ? 'on' : ''}" data-tool="${id}" title="${name}">${ic(icon)}<span>${name}</span></button>`).join('')}
    <div class="grow"></div><button class="btn sm ghost" data-a="undo" title="Отменить (Ctrl+Z)">${ic('undo')}</button><button class="btn sm ghost" data-a="redo" title="Вернуть (Ctrl+Y)">${ic('redo')}</button></div>
    <div class="sk-colors"><input type="color" id="skColor" value="${SK.color}" title="Свой цвет">
      ${PALETTE.map((c) => `<button class="sk-swatch ${c === SK.color ? 'on' : ''}" data-c="${c}" style="--c:${c}" title="${c}"></button>`).join('')}</div>
    <div class="sk-recent-row"><span class="small faint">Недавние:</span><div id="skRecent" class="sk-recent">${SK.recent.map((c) => `<button class="sk-swatch" data-c="${c}" style="--c:${c}"></button>`).join('')}</div></div>`;
}

function bindTools(panel) {
  panel.addEventListener('click', (e) => {
    const t = e.target.closest('[data-tool]');
    if (t) { SK.tool = t.dataset.tool; $$('.sk-tool', panel).forEach((b) => b.classList.toggle('on', b === t)); }
    const sw = e.target.closest('.sk-swatch');
    if (sw) setColor(sw.dataset.c);
    const a = e.target.closest('[data-a]')?.dataset.a;
    if (a === 'undo') undo();
    if (a === 'redo') redoStep();
  });
  const col = $('#skColor', panel);
  if (col) col.oninput = (e) => setColor(e.target.value);
}

function undo() { if (!SK.undo.length) return; SK.redo.push(snapshot()); SK.ctx.putImageData(SK.undo.pop(), 0, 0); redraw(); }
function redoStep() { if (!SK.redo.length) return; SK.undo.push(snapshot()); SK.ctx.putImageData(SK.redo.pop(), 0, 0); redraw(); }

document.addEventListener('keydown', (e) => {
  if (S.page !== 'skins' || !(e.ctrlKey || e.metaKey) || e.target.matches('input, textarea')) return;
  if (e.key.toLowerCase() === 'z') { e.preventDefault(); undo(); }
  if (e.key.toLowerCase() === 'y') { e.preventDefault(); redoStep(); }
});

function renderPanel(el) {
  // каждый раз — новый блок: раньше обработчики нажатий копились на одном и том же элементе, и после нескольких
  // переключений вкладок одно нажатие «Отменить» откатывало несколько шагов, а узор плаща накладывался дважды
  const old = $('#skBody', el);
  const panel = document.createElement('div');
  panel.className = 'sk-body'; panel.id = 'skBody';
  old.replaceWith(panel);
  if (SK.tab === 'editor') {
    panel.innerHTML = `${toolsHtml()}
      <div class="row" style="margin:6px 0">${segHtml('sklayer', [['base', 'Основа'], ['over', 'Одежда (2-й слой)']], SK.layer)}
        <span class="small faint grow">Рисуйте на схеме или прямо на персонаже слева.</span></div>
      <div class="sk-uv-wrap"><canvas id="skUV"></canvas></div>`;
    bindSegs(panel, (n, v) => { if (n === 'sklayer') { SK.layer = v; redraw(); } });
    strokeHandler($('#skUV', panel), (fx, fy) => [Math.floor(fx * 64), Math.floor(fy * 64)], () => SK.ctx, 64, 64, (u, v) => isOverlay(u, v));
    bindTools(panel);
  } else if (SK.tab === 'cape') {
    panel.innerHTML = `${toolsHtml()}
      <div class="sk-cape-row"><div><div class="small muted" style="margin-bottom:6px">Плащ (рамка — сторона, которую видят сзади)</div><canvas id="skCapeUV"></canvas></div>
        <div class="grow"><div class="small muted">Готовые узоры — цвета берутся из двух выбранных ниже</div>
          <div class="row" style="margin:8px 0"><input type="color" id="capeA" value="${SK.capeA || '#5b4bd6'}"><input type="color" id="capeB" value="${SK.capeB || '#f6c84a'}"></div>
          <div class="sk-presets">${CAPE_PRESETS.map((p) => `<button class="sk-preset" data-cape="${p.id}"><canvas data-cape-prev="${p.id}"></canvas><span>${esc(p.name)}</span></button>`).join('')}</div>
          <button class="btn ghost sm" data-a="nocape" style="margin-top:10px">${ic('trash')} Убрать плащ</button></div></div>`;
    const drawPresets = () => $$('[data-cape-prev]', panel).forEach((cv) => {
      const cape = makeCape(cv.dataset.capePrev, $('#capeA', panel).value, $('#capeB', panel).value);
      cv.width = 40; cv.height = 64; const x = cv.getContext('2d'); x.imageSmoothingEnabled = false;
      x.drawImage(cape, 1, 1, 10, 16, 0, 0, 40, 64);
    });
    drawPresets();
    $('#capeA', panel).oninput = (e) => { SK.capeA = e.target.value; drawPresets(); };
    $('#capeB', panel).oninput = (e) => { SK.capeB = e.target.value; drawPresets(); };
    panel.addEventListener('click', (e) => {
      const p = e.target.closest('[data-cape]');
      if (p) {
        const cape = makeCape(p.dataset.cape, $('#capeA', panel).value, $('#capeB', panel).value);
        SK.capeCtx.clearRect(0, 0, 64, 32); SK.capeCtx.drawImage(cape, 0, 0); SK.hasCape = true; SK.capeDirty = true; redraw();
      }
      if (e.target.closest('[data-a=nocape]')) { SK.capeCtx.clearRect(0, 0, 64, 32); SK.hasCape = false; SK.capeDirty = true; redraw(); }
    });
    strokeHandler($('#skCapeUV', panel), (fx, fy) => [Math.floor(fx * 22), Math.floor(fy * 17)], () => { SK.capeDirty = true; return SK.capeCtx; }, 64, 32, () => true);
    bindTools(panel);
  } else if (SK.tab === 'gear') {
    panel.innerHTML = `<p class="small muted">Аксессуары рисуются во втором слое скина — их видно в игре всем, как и сам скин. Цвет — из палитры.</p>
      ${toolsHtml()}
      <div class="sk-presets gear">${GEAR.map((g) => `<button class="sk-preset" data-gear="${g.id}"><canvas data-gear-prev="${g.id}"></canvas><span>${esc(g.name)}</span></button>`).join('')}</div>
      <div class="row" style="margin-top:10px"><button class="btn ghost sm" data-a="cleargear">${ic('trash')} Снять все аксессуары (очистить 2-й слой головы и тела)</button></div>`;
    const prev = () => $$('[data-gear-prev]', panel).forEach((cv) => {
      const tmp = skCanvas(64, 64), tx = tmp.getContext('2d');
      tx.drawImage(SK.canvas, 0, 0); applyGear(tx, cv.dataset.gearPrev, SK.color);
      const save = SK.canvas; SK.canvas = tmp; cv.width = 20 * 3; cv.height = 12 * 3; const x = cv.getContext('2d'); x.imageSmoothingEnabled = false;
      x.clearRect(0, 0, cv.width, cv.height);
      for (const layer of ['base', 'over']) {
        const [sx, sy, w, h] = layer === 'base' ? UV.head.front : UV.head.o.front;
        x.drawImage(tmp, sx, sy, w, h, 6 * 3, 2 * 3, 8 * 3, 8 * 3);
      }
      SK.canvas = save;
    });
    prev();
    bindTools(panel);
    panel.addEventListener('click', (e) => {
      const g = e.target.closest('[data-gear]');
      if (g) { pushUndo(); applyGear(SK.ctx, g.dataset.gear, SK.color); redraw(); prev(); }
      if (e.target.closest('.sk-swatch') || e.target.closest('#skColor')) setTimeout(prev, 0);
      if (e.target.closest('[data-a=cleargear]')) { pushUndo(); SK.ctx.clearRect(32, 0, 32, 16); SK.ctx.clearRect(16, 32, 24, 16); redraw(); prev(); }
    });
    $('#skColor', panel).addEventListener('input', () => prev());
  } else {
    renderCatalogPanel(panel);
  }
  hydrateIcons(panel);
}

/* каталог: свои скины + Ely.by по нику + по ссылке + из файла */
function renderCatalogPanel(panel) {
  const items = OUTFITS.map((_, n) => makeCatalogSkin(n));
  panel.innerHTML = `
    <div class="sk-import">
      <div class="field"><span>Скин игрока Ely.by по нику</span><div class="row"><input class="input" id="elyNick" placeholder="Ник на Ely.by"><button class="btn" data-a="ely">${ic('download')} Взять</button>
        <button class="btn ghost" data-a="elysite" title="Открыть каталог Ely.by">${ic('link')} Каталог Ely.by</button></div></div>
      <div class="field"><span>По ссылке на картинку .png</span><div class="row"><input class="input" id="skUrl" placeholder="https://...png"><button class="btn" data-a="url">${ic('download')} Загрузить</button></div></div>
      <div class="row"><button class="btn ghost" data-a="file">${ic('upload')} Из файла на компьютере</button></div>
    </div>
    <div class="small muted" style="margin:14px 0 8px">Скины VaultMC — нарисованы для лаунчера, можно брать и перекрашивать:</div>
    <div class="sk-catalog">${items.map((s, n) => `<button class="sk-card" data-n="${n}"><canvas data-cat="${n}"></canvas><span>${esc(s.name)}</span></button>`).join('')}</div>`;
  $$('[data-cat]', panel).forEach(async (cv) => {
    const img = await loadImg(items[+cv.dataset.cat].url);
    const save = SK.canvas, saveSlim = SK.slim, saveCape = SK.hasCape;
    SK.canvas = skCanvas(64, 64); SK.canvas.getContext('2d').drawImage(img, 0, 0); SK.slim = false; SK.hasCape = false;
    cv.width = 20 * 4; cv.height = 36 * 4; drawView(cv, 'front', 4, false);
    SK.canvas = save; SK.slim = saveSlim; SK.hasCape = saveCape;
  });
  const useUrl = applySkin;
  panel.onclick = async (e) => {
    const card = e.target.closest('[data-n]');
    if (card) return useUrl(items[+card.dataset.n].url, `Скин «${items[+card.dataset.n].name}»`);
    const a = e.target.closest('[data-a]')?.dataset.a;
    if (a === 'elysite') api.open_url('https://ely.by/skins');
    if (a === 'ely' || a === 'url') {
      const r = await safe(a === 'ely' ? api.skin_from_elyby($('#elyNick', panel).value) : api.skin_from_url($('#skUrl', panel).value));
      if (r && r.error) return toast(r.error, 'error');
      if (r) useUrl(r.skin, a === 'ely' ? 'Скин с Ely.by' : 'Скин по ссылке', a !== 'ely');
    }
    if (a === 'file') importSkinFile();
  };
}

/** Ставит скин в редактор (не сохраняет). check — проверить, что это правда скин. */
async function applySkin(url, label, check = false) {
  if (!SK.info.editable) return toast('Ставить скины можно оффлайн-аккаунту', 'warn');
  if (check) { const why = await checkSkin(url); if (why) return toast(why, 'error', 8000); }
  pushUndo();
  const img = await loadImg(url);
  const c = await skinToCanvas(url);
  const slim = img.height === img.width && detectSlim(c);
  SK.ctx.clearRect(0, 0, 64, 64); SK.ctx.drawImage(c, 0, 0); setSlim(slim); redraw();
  toast(`${label}${slim ? ' (тонкие руки — включено само)' : ''} — нажмите «Сохранить», чтобы оставить`);
}

async function importSkinFile() {
  if (!SK.info.editable) return toast('Ставить скины можно оффлайн-аккаунту', 'warn');
  const r = await safe(api.pick_skin_file());
  if (!r || (!r.ok && !r.error)) return;
  if (r.error) return toast(r.error, 'error', 8000);
  applySkin(r.skin, 'Скин из файла', true);
}

async function saveStudio() {
  // галочка «Тонкие руки» не совпадает с тем, как нарисован скин, — иначе в игре руки сломаются
  if (!SK.slim && detectSlim(SK.canvas)) {
    if (!await confirmDialog('У этого скина тонкие руки',
      'Руки нарисованы шириной 3 пикселя (модель Alex). Если сохранить как обычный скин, у рук появится пустая полоса — и в игре у друзей тоже.',
      'Включить тонкие руки и сохранить')) return;
    setSlim(true); redraw();
  } else if (SK.slim && hasClassicArms(SK.canvas)) {
    if (!await confirmDialog('У этого скина обычные руки',
      'Руки нарисованы шириной 4 пикселя (модель Steve). С тонкими руками крайний столбец рисунка на руках пропадёт.',
      'Сделать обычные руки и сохранить')) return;
    setSlim(false); redraw();
  }
  const btn = SK.root && $('[data-a=save]', SK.root);
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Сохраняю...'; }
  const r = await safe(api.save_skin(SK.canvas.toDataURL('image/png'), SK.slim));
  let capeErr = '';
  if (r && r.ok && SK.capeDirty) {
    const c = await safe(api.save_cape(SK.hasCape ? SK.cape.toDataURL('image/png') : ''));
    capeErr = (c && (c.error || c.serverError)) || '';
    SK.capeDirty = false;
  }
  if (btn) { btn.disabled = false; btn.innerHTML = `${ic('check')} Сохранить${SK.info.server ? ' и показать всем' : ''}`; hydrateIcons(btn); }
  if (!r) return;
  if (r.error) return toast(r.error, 'error');
  SK.dirty = false;
  S.skins[S.account.id] = r.skin; renderAccount();
  const err = r.serverError || capeErr;
  if (err) toast(`Сохранено у вас, но на сервер не ушло: ${err}`, 'warn', 9000);
  else toast(SK.info.server ? 'Готово! Скин увидят все, кто играет через VaultMC с модами' : 'Скин сохранён');
}

async function resetSkin(el) {
  const text = SK.info.server
    ? 'Ваш скин удалится у вас и на сервере VaultMC — друзья увидят стандартный (или ваш скин с Ely.by, если он есть под этим ником). Плащ останется.'
    : 'Ваш скин удалится, вернётся стандартный. Плащ останется.';
  if (!await confirmDialog('Сбросить скин?', text, 'Сбросить', true)) return;
  const r = await safe(api.reset_skin());
  if (!r) return;
  if (r.error) return toast(r.error, 'error');
  S.skins[S.account.id] = ''; renderAccount();
  await PAGES.skins.render(el);
  if (r.serverError) toast(`Сброшено у вас, но сервер не ответил: ${r.serverError}`, 'warn', 9000);
  else toast('Скин сброшен');
}

async function exportSkin() {
  const r = await safe(api.export_skin(SK.canvas.toDataURL('image/png'), `${S.account?.username || 'skin'}.png`));
  if (r && r.ok) toast('Скин сохранён в файл'); else if (r && r.error) toast(r.error, 'error');
}
