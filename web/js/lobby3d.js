/* Главный экран: персонаж в настоящем 3D (CSS 3D, как в мастерской скинов): голова, тело, руки, ноги, второй слой,
 * объёмный блок травы. Следит за курсором (голова, потом тело), дышит. По клику приседает, прыгает, поднимает руку,
 * в кулаке появляется предмет (алмаз, золото или изумруд) — объёмный, как выпавший в Minecraft, — и взлетает над рукой.
 * Если скина нет (картинка не загрузилась) — рисуется старый плоский персонаж drawSkinRig из pixel.js.
 * Части и развёртки берём из skin3d.js (S3_PARTS, s3Box, s3Normalize).
 */

const LH_FACE_ORDER = ['front', 'back', 'right', 'left', 'top', 'bottom'];
const LH_FACE_LIGHT = { front: 1, back: .66, right: .84, left: .84, top: 1.12, bottom: .55 };   // лёгкая «подсветка» граней — объём заметнее

function lhEl(cls, tag = 'div') { const e = document.createElement(tag); if (cls) e.className = cls; return e; }
const lhEase = (x) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x));
const lhSeg = (t, a, b) => lhEase((t - a) / (b - a));

function lhShade(box) {
  [...box.children].forEach((f, i) => { const k = LH_FACE_LIGHT[LH_FACE_ORDER[i]]; if (k && k !== 1) f.style.filter = `brightness(${k})`; });
}

// развёртка блока травы 22×8×22: сверху трава, с боков земля с травяной кромкой
function lhGroundTexture() {
  const c = document.createElement('canvas'); c.width = 88; c.height = 30;
  const g = c.getContext('2d'), r = rng(7);
  const grass = ['#5fae3c', '#4f9a32', '#6cbf44'], dirt = ['#7a5434', '#6b4a2e', '#86603d', '#5e4128'];
  const px = (x, y, col) => { g.fillStyle = col; g.fillRect(x, y, 1, 1); };
  for (let y = 0; y < 22; y++) for (let x = 0; x < 22; x++) px(22 + x, y, grass[Math.floor(r() * 3)]);       // верх
  for (let y = 0; y < 22; y++) for (let x = 0; x < 22; x++) px(44 + x, y, dirt[Math.floor(r() * 4)]);       // низ
  for (const x0 of [22, 66, 0, 44]) {                                                                         // 4 боковые грани
    for (let y = 0; y < 8; y++) for (let x = 0; x < 22; x++) {
      const isGrass = y < 2 || (y === 2 && r() < 0.5);
      px(x0 + x, 22 + y, isGrass ? grass[Math.floor(r() * 3)] : dirt[Math.floor(r() * 4)]);
    }
  }
  return c;
}

// текстура сундука (web/img/chest-diffuse.png, 4096 → 512): из неё берём цвет досок; null — рисуем процедурно
const LH_CHEST_TEX = 'img/chest-diffuse.png';
// куски развёртки сундука в пикселях 64×64 (стандартная раскладка Minecraft): [x, y, ширина, высота]
const LH_UV = {
  base: { top: [14, 19, 14, 14], bottom: [28, 19, 14, 14], front: [14, 33, 14, 10], back: [42, 33, 14, 10], left: [0, 33, 14, 10], right: [28, 33, 14, 10] },
  lid: { top: [14, 0, 14, 14], bottom: [28, 0, 14, 14], front: [14, 14, 14, 5], back: [42, 14, 14, 5], left: [0, 14, 14, 5], right: [28, 14, 14, 5] },
};
let lhChestAtlas = null;   // canvas | false (не загрузилась) | null (ещё не пробовали)
async function lhLoadChestAtlas() {
  if (lhChestAtlas !== null) return lhChestAtlas;
  const img = await loadImage(LH_CHEST_TEX);
  if (!img) { lhChestAtlas = false; return false; }
  const c = document.createElement('canvas'); c.width = 512; c.height = 512;
  c.getContext('2d').drawImage(img, 0, 0, 512, 512);
  lhChestAtlas = c;
  return c;
}

// развёртка досок для сундука: размеры [w, h, d] как у s3Box; latch — золотая застёжка спереди (на крышке)
// K — во сколько раз развёртка крупнее «блоков» сундука: каждый блок делится на K×K точек текстуры, поэтому доски видны
const LH_WOOD_K = 8;
function lhWood(w, h, d, seed, latch, K = LH_WOOD_K) {
  const c = document.createElement('canvas'); c.width = K * (2 * d + 2 * w); c.height = K * (d + h);
  const g = c.getContext('2d');
  const wood = ['#9a6532', '#8a5a2b', '#a46f3a', '#86572a'];
  const atlas = lhChestAtlas || null;
  const px = atlas ? atlas.getContext('2d').getImageData(0, 0, 512, 512).data : null;
  const faces = s3Faces(w, h, d);
  const uv = LH_UV[latch === true ? 'lid' : 'base'];
  for (const [name, [fx, fy, fw, fh]] of Object.entries(faces)) {
    const sw = fw * K, sh = fh * K;
    const [ux, uy, uw, uh] = uv[name] || [0, 0, 1, 1];   // кусок развёртки этой грани, в пикселях 64×64
    for (let sy = 0; sy < sh; sy++) for (let sx = 0; sx < sw; sx++) {
      const edge = sx < 2 || sy < 2 || sx >= sw - 2 || sy >= sh - 2;   // тёмный железный контур по краям граней
      let col = wood[(Math.floor(sy / 4) + Math.floor(sx / 4)) % 4];   // доски (без текстуры)
      if (px) {   // цвет из текстуры: кусок развёртки этой грани, растянутый на грань
        const ax = Math.floor((ux + (sx + 0.5) * uw / sw) * 8);
        const ay = Math.floor((uy + (sy + 0.5) * uh / sh) * 8);
        const i = (ay * 512 + ax) * 4;
        col = `rgb(${px[i]},${px[i + 1]},${px[i + 2]})`;
      }
      if (edge) col = '#2b2b2f';
      g.fillStyle = col; g.fillRect(fx * K + sx, fy * K + sy, 1, 1);
    }
  }
  if (latch) {   // железный замок как в игре: маленькая серая застёжка (2×4), 3 ряда на крышке и 1 на основании
    const [fx, fy, fw, fh] = faces.front, cx = (fx + Math.floor(fw / 2)) * K;
    const onLid = latch === true;
    const y0 = onLid ? (fy + fh - 3) * K : fy * K, y1 = onLid ? (fy + fh) * K : fy * K + K;
    g.fillStyle = '#26282d'; g.fillRect(cx - K, y0, 2 * K, y1 - y0);                       // тёмный контур
    const grad = g.createLinearGradient(0, y0, 0, y1);
    grad.addColorStop(0, '#b4bac4'); grad.addColorStop(1, '#8a909b');                      // приглушённый металл, не белый
    g.fillStyle = grad; g.fillRect(cx - K + 3, onLid ? y0 + 3 : y0, 2 * K - 6, y1 - y0 - (onLid ? 3 : 3));
    if (!onLid) { g.fillStyle = '#26282d'; g.fillRect(cx - 2, y0 + 1, 4, 4); }              // замочная скважина
  }
  return c;
}

// предмет 8×8 «выпавший в Minecraft»: несколько одинаковых слоёв друг за другом дают толщину при вращении
function lhGem(kind, U) {
  const src = document.createElement('canvas'); src.width = 8; src.height = 8;
  drawLoot(src.getContext('2d'), kind);
  const gem = lhEl('lh-gem');
  const N = 7, S = 1.15, size = 8 * S * U;
  for (let i = 0; i < N; i++) {
    const c = document.createElement('canvas'); c.width = 8; c.height = 8;
    c.getContext('2d').drawImage(src, 0, 0);
    c.style.cssText = `width:${size}px;height:${size}px;margin:${-size / 2}px 0 0 ${-size / 2}px;`
      + `transform:translateZ(${((i - (N - 1) / 2) * 0.42 * U).toFixed(2)}px);`
      + (i && i < N - 1 ? 'filter:brightness(.82);' : '');
    gem.append(c);
  }
  return gem;
}

function lhClearItem(st) {
  if (st.item) { st.item.outer.remove(); if (st.item.burst) st.item.burst.remove(); st.item = null; }
  st.sparks = false;
}

const LH_GLOW = { diamond: '74, 230, 239', gold: '242, 192, 55', emerald: '47, 208, 106' };

async function drawSkinRig3d(container, skinUrl, accent) {
  const img = skinUrl ? await loadImage(skinUrl) : null;
  if (!img) return drawSkinRig(container, skinUrl, accent);
  const info = skinInfo(img);
  const [tex, k] = s3Normalize(img);
  const st = {
    x: 0, y: 0, tx: 0, ty: 0, last: 0, t0: performance.now(), raf: 0,
    click: 0, fixedT: null, item: null, sparks: false, parts: {}, U: 8,
  };

  const build = () => {
    const U = Math.max(3, (container.clientHeight || 320) / 40);
    st.U = U;
    container.innerHTML = '';
    st.parts = {};
    const scene = lhEl('lh-scene');
    scene.style.top = 16 * U + 'px';
    scene.style.perspective = 85 * U + 'px';
    const world = lhEl('lh-world');
    const char = lhEl('lh-char');
    char.style.transformOrigin = `0 ${16 * U}px 0`;

    // блок травы под ногами (верх на уровне ступней: y=32 → 16 от центра) и мягкая тень
    const gp = lhEl('s3-pivot');
    gp.style.transform = `translate3d(0, ${20 * U}px, 0)`;
    const gb = s3Box(lhGroundTexture(), 0, 0, [22, 8, 22], U, [1, 1, 1], 1);
    lhShade(gb); gp.append(gb);
    const shadow = lhEl('lh-shadow');
    shadow.style.cssText = `width:${15 * U}px;height:${10 * U}px;margin:${-5 * U}px 0 0 ${-7.5 * U}px;`
      + `transform:translate3d(0, ${16 * U - 0.2}px, 0) rotateX(90deg)`;
    st.shadow = shadow;
    world.append(gp, shadow);

    for (const [name, p] of Object.entries(S3_PARTS)) {
      const slimArm = p.arm && info.slim;
      const size = slimArm ? [3, 12, 4] : p.size;
      const pos = slimArm ? [p.pos[0] - p.arm * 0.5, p.pos[1], p.pos[2]] : p.pos;
      const piv = lhEl('s3-pivot');
      piv.style.setProperty('--px', `${p.pivot[0] * U}px`);
      piv.style.setProperty('--py', `${(p.pivot[1] - 16) * U}px`);
      piv.style.setProperty('--pz', `${p.pivot[2] * U}px`);
      const off = lhEl('s3-off');
      off.style.transform = `translate3d(${(pos[0] - p.pivot[0]) * U}px,${(pos[1] - p.pivot[1]) * U}px,${(pos[2] - p.pivot[2]) * U}px)`;
      const main = s3Box(tex, p.uv[0], p.uv[1], size, U, [1, 1, 1], k); lhShade(main); off.append(main);
      if (name !== 'head' || info.hat) {
        const over = s3Box(tex, p.o[0], p.o[1], size, U, size.map((v) => (v + p.grow * 2) / v), k);
        lhShade(over); off.append(over);
      }
      piv.append(off);
      char.append(piv);
      st.parts[name] = piv;
    }
    // сундук-«хранилище» перед персонажем (лежит в группе персонажа, но каждый кадр компенсируем его прыжок и поворот)
    const cw = 10, chh = 5, cd = 7, lidH = 2;
    const chest = lhEl('lh-chestg');
    const base = lhEl('s3-pivot'); base.style.transform = `translate3d(0,${-(chh / 2) * U}px,0)`;
    const bb = s3Box(lhWood(cw, chh, cd, 11, 'base'), 0, 0, [cw, chh, cd], U, [1, 1, 1], LH_WOOD_K); lhShade(bb); base.append(bb);
    const lidPivot = lhEl('s3-pivot');
    const lidOff = lhEl('s3-off'); lidOff.style.transform = `translate3d(0,${-(lidH / 2) * U}px,${(cd / 2) * U}px)`;
    const lb = s3Box(lhWood(cw, lidH, cd, 12, true), 0, 0, [cw, lidH, cd], U, [1, 1, 1], LH_WOOD_K); lhShade(lb); lidOff.append(lb); lidPivot.append(lidOff);
    const cglow = lhEl('lh-chestglow');
    cglow.style.cssText = `width:${(cw - 1.5) * U}px;height:${(cd - 1.5) * U}px;margin:${-(cd - 1.5) / 2 * U}px 0 0 ${-(cw - 1.5) / 2 * U}px;`
      + `transform:translate3d(0,${-chh * U + 1}px,0) rotateX(90deg);`;
    chest.append(base, lidPivot, cglow);
    char.append(chest);
    st.chest = chest; st.lid = lidPivot; st.chestGlow = cglow; st.chestH = chh; st.chestD = cd;
    world.append(char);
    scene.append(world);
    container.append(scene);
    st.scene = scene; st.world = world; st.char = char;
    st.item = null; st.sparks = false;
  };

  const setPart = (name, rx, ry = 0, rz = 0) => {
    const p = st.parts[name];
    if (p) p.style.transform = `translate3d(var(--px),var(--py),var(--pz)) rotateY(${ry.toFixed(2)}deg) rotateX(${rx.toFixed(2)}deg) rotateZ(${rz.toFixed(2)}deg)`;
  };

  const spawnItem = (T) => {
    const kind = ['diamond', 'gold', 'emerald'][Math.floor(Math.random() * 3)];
    const outer = lhEl('lh-item');
    outer.style.setProperty('--glow', LH_GLOW[kind]);
    const glow = lhEl('lh-glow'); const gem = lhGem(kind, st.U);
    outer.append(glow, gem);
    // искры из сундука (кладём в сам сундук: он стоит на месте)
    const burst = lhEl('lh-item');
    burst.style.setProperty('--glow', LH_GLOW[kind]);
    burst.style.transform = `translate3d(0,${-(st.chestH + 0.5) * st.U}px,0)`;
    for (let i = 0; i < 10; i++) {
      const a = Math.random() * Math.PI - Math.PI, d = 3 + Math.random() * 5;       // вверх и в стороны
      const sp = lhEl('lh-spark');
      sp.style.cssText = `--dx:${(Math.cos(a) * d).toFixed(1)};--dy:${(Math.sin(a) * d - 2).toFixed(1)};--d:${(Math.random() * 0.1).toFixed(2)}s;`;
      burst.append(sp);
    }
    st.chest.append(burst);
    st.char.append(outer);
    st.item = { outer, glow, gem, kind, burst };
    st.sparks = false;
  };

  const addSparks = () => {
    st.sparks = true;
    for (let i = 0; i < 10; i++) {
      const a = (i / 10) * Math.PI * 2 + Math.random() * 0.5, d = 4.5 + Math.random() * 5;
      const s = lhEl('lh-spark');
      s.style.cssText = `--dx:${(Math.cos(a) * d).toFixed(1)};--dy:${(Math.sin(a) * d).toFixed(1)};--d:${(Math.random() * 0.2).toFixed(2)}s;`;
      st.item.outer.append(s);
    }
  };

  const frame = (now) => {
    st.raf = 0;
    if (!container.isConnected) { ro.disconnect(); return; }
    if (document.body.classList.contains('ui-idle')) return;     // простой: кадры не рисуем, uiWake вернёт
    if (!document.hidden && st.char) {
      const t = (now - st.t0) / 1000, U = st.U;
      // куда смотрим: на курсор; когда курсор ушёл — остаётся в последнем положении. Оглядывается сам только до первого движения мыши
      let tx = st.tx, ty = st.ty;
      if (!st.last) {
        tx = Math.sin(t * 0.45) * 0.45 + Math.sin(t * 0.17) * 0.2;
        ty = Math.sin(t * 0.3 + 1.3) * 0.18;
      }
      st.x += (tx - st.x) * 0.075; st.y += (ty - st.y) * 0.075;

      // щелчок: T — секунды от клика (0…1.5). Ход: тянется рукой к сундуку → крышка открывается, свет, из сундука вылетает предмет →
      // дугой летит в поднятую руку → прыжок, сундук закрывается → предмет взлетает над рукой и гаснет
      let T = st.fixedT !== null ? st.fixedT : (st.click ? (now - st.click) / 1000 : -1);
      if (T > 1.5) { T = -1; st.click = 0; lhClearItem(st); container.classList.remove('hopping'); }
      const act = T >= 0;
      const bump = (a, b) => Math.sin(Math.PI * Math.min(1, Math.max(0, (T - a) / (b - a))));
      const crouch = act ? bump(0, 0.14) : 0, jump = act ? bump(0.55, 0.9) : 0, land = act ? bump(0.9, 1.1) : 0;
      const point = act ? lhSeg(T, 0, 0.14) : 0;                                     // рука тянется к сундуку
      const armRx = act ? 55 * point + 45 * lhSeg(T, 0.3, 0.72) - 100 * lhSeg(T, 1.05, 1.4) : 0;
      const armUp = armRx / 100;
      const breathe = Math.sin(t * 1.6);

      const dy = (0.8 * crouch - 3 * jump + 0.5 * land + breathe * 0.12) * U;
      const sy = 1 - 0.05 * crouch + 0.025 * jump - 0.04 * land;
      const yaw = st.x * 16;
      st.char.style.transform = `translate3d(0,${dy.toFixed(2)}px,0) rotateY(${yaw.toFixed(2)}deg) scale3d(${(2 - sy).toFixed(3)},${sy.toFixed(3)},${(2 - sy).toFixed(3)})`;
      st.world.style.transform = `rotateX(${(-9 + st.y * -3).toFixed(2)}deg) rotateY(${(-14 + st.x * 8 + Math.sin(t * 0.5) * 3).toFixed(2)}deg)`;
      if (st.shadow) st.shadow.style.opacity = (1 - 0.45 * jump).toFixed(2);

      // сундук стоит на месте (компенсируем прыжок и поворот персонажа); крышка и свет
      const cx = -6.5 * U, cy = 16 * U, cz = 6.5 * U;
      st.chest.style.transform = `rotateY(${(-yaw).toFixed(2)}deg) translate3d(0,${(-dy).toFixed(2)}px,0) translate3d(${cx}px,${cy}px,${cz}px)`;
      const open = act ? lhSeg(T, 0.12, 0.3) * (1 + 0.08 * Math.sin(Math.PI * lhSeg(T, 0.12, 0.3))) - lhSeg(T, 0.95, 1.2) : 0;
      st.lid.style.transform = `translate3d(0,${-st.chestH * U}px,${-(st.chestD / 2) * U}px) rotateX(${(open * 108).toFixed(2)}deg)`;
      st.chestGlow.style.opacity = Math.max(0, Math.min(1, open) * (st.item ? 0.9 : 0.7)).toFixed(2);

      const sw = Math.sin(t * 1.3) * 3;
      const rx = armRx, rz = 3 + armUp * 7 + 10 * point * (1 - lhSeg(T, 0.3, 0.72)) - breathe * 1.2;
      setPart('rarm', rx + (act ? 0 : sw), 0, rz);
      setPart('larm', -sw - armUp * 10, 0, -(3 - breathe * 1.2) - armUp * 5);
      setPart('rleg', -sw * 0.4); setPart('lleg', sw * 0.4);
      setPart('head', -st.y * 16 - 8 * point * (1 - lhSeg(T, 0.3, 0.7)), st.x * 26 - 10 * point * (1 - lhSeg(T, 0.3, 0.7)), st.x * 1.5);
      setPart('body', 0);

      // предмет вылетает из открытого сундука
      if (act && T >= 0.26 && !st.item) spawnItem(T);
      if (st.item) {
        const it = st.item, a = (rx * Math.PI) / 180, zr = (rz * Math.PI) / 180, ps = (yaw * Math.PI) / 180;
        const u = lhSeg(T, 0.26, 0.72);
        const reach = (12 + 3 * u) * U;
        const sh = { x: -6 * U + (info.slim ? 0.5 * U : 0), y: -6 * U, z: 0 };                    // плечо правой руки
        const hand = {
          x: sh.x - reach * Math.sin(zr),
          y: sh.y + reach * Math.cos(zr) * Math.cos(a),
          z: sh.z + reach * Math.cos(zr) * Math.sin(a),
        };
        // точка над сундуком в координатах персонажа (персонаж повёрнут на yaw и сдвинут на dy)
        const wx = cx, wy = cy - (st.chestH + 1) * U, wz = cz;
        const start = { x: wx * Math.cos(ps) - wz * Math.sin(ps), y: wy - dy, z: wx * Math.sin(ps) + wz * Math.cos(ps) };
        const rise = 4.5 * U * lhSeg(T, 1.0, 1.45);
        const x = start.x + (hand.x - start.x) * u, z = start.z + (hand.z - start.z) * u;
        const y = start.y + (hand.y - start.y) * u - 7 * U * Math.sin(Math.PI * u) - rise;           // дуга вверх и в руку
        const s0 = lhSeg(T, 0.26, 0.5), scale = (0.4 + 0.6 * s0) * (1 + 0.2 * Math.sin(Math.PI * s0));
        const fade = 1 - lhSeg(T, 1.15, 1.5);
        it.outer.style.transform = `translate3d(${x.toFixed(2)}px,${y.toFixed(2)}px,${z.toFixed(2)}px)`;
        it.outer.style.opacity = fade.toFixed(2);
        it.gem.style.transform = `rotateY(${((T - 0.26) * 760).toFixed(1)}deg) scale(${scale.toFixed(3)})`;
        it.glow.style.opacity = (Math.min(1, s0 * 1.5) * (0.6 + 0.4 * Math.sin(Math.PI * Math.min(1, (T - 0.26) / 0.5)))).toFixed(2);
        it.glow.style.transform = `scale(${(0.5 + s0 * 0.8 + lhSeg(T, 0.9, 1.5) * 0.5).toFixed(3)})`;
        if (!st.sparks && T >= 0.7) addSparks();           // «поймал» — искры
      }
    }
    if (st.fixedT === null) st.raf = requestAnimationFrame(frame);
  };

  const api3 = {
    look(clientX, clientY) {
      if (clientX === null || clientX === undefined) { /* курсор ушёл — остаёмся в последнем положении */ } else {
        const r = container.getBoundingClientRect();
        st.tx = Math.max(-1, Math.min(1, (clientX - (r.left + r.width / 2)) / 420));
        st.ty = Math.max(-1, Math.min(1, (clientY - (r.top + r.height * 0.3)) / 420));
        st.last = performance.now();
      }
      if (!st.raf && st.fixedT === null) st.raf = requestAnimationFrame(frame);
    },
    wake() { if (!st.raf && st.fixedT === null) st.raf = requestAnimationFrame(frame); },
    click() {
      if (st.click) return;
      st.click = performance.now();
      container.classList.add('hopping');
      api3.wake();
    },
    seek(T) { st.fixedT = T; if (T === null) api3.wake(); else frame(performance.now()); },   // для проверки по кадрам
  };
  container.__lh = api3;
  container.classList.add('lh-host');
  container.onclick = () => api3.click();
  await lhLoadChestAtlas();

  let resizeT = 0;
  const ro = new ResizeObserver(() => { clearTimeout(resizeT); resizeT = setTimeout(build, 120); });
  ro.observe(container);
  build();
  api3.wake();
}
