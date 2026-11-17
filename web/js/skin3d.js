/* 3D-просмотр скина на чистом CSS 3D (без WebGL и сторонних библиотек).
 * Персонаж собирается из коробок: голова, тело, руки, ноги + второй слой (чуть больше) + плащ.
 * Мышью — вращать, колесом — приблизить. Ходьба, второй слой и плащ включаются кнопками.
 */

// [u, v] развёртки и размеры коробки [w, h, d] на скине 64×64; o — второй слой
const S3_PARTS = {
  head: { uv: [0, 0], o: [32, 0], size: [8, 8, 8], pos: [0, 4, 0], pivot: [0, 8, 0], grow: 1 },
  body: { uv: [16, 16], o: [16, 32], size: [8, 12, 4], pos: [0, 14, 0], pivot: [0, 8, 0], grow: 0.5 },
  rarm: { uv: [40, 16], o: [40, 32], size: [4, 12, 4], pos: [-6, 14, 0], pivot: [-6, 10, 0], grow: 0.5, arm: -1 },
  larm: { uv: [32, 48], o: [48, 48], size: [4, 12, 4], pos: [6, 14, 0], pivot: [6, 10, 0], grow: 0.5, arm: 1 },
  rleg: { uv: [0, 16], o: [0, 32], size: [4, 12, 4], pos: [-2, 26, 0], pivot: [-2, 20, 0], grow: 0.5 },
  lleg: { uv: [16, 48], o: [0, 48], size: [4, 12, 4], pos: [2, 26, 0], pivot: [2, 20, 0], grow: 0.5 },
};

// Грани коробки на развёртке Minecraft: [x, y, ширина, высота] относительно (u, v)
function s3Faces(w, h, d) {
  return {
    front: [d, d, w, h], back: [2 * d + w, d, w, h],
    right: [0, d, d, h], left: [d + w, d, d, h],
    top: [d, 0, w, d], bottom: [d + w, 0, w, d],
  };
}

function s3Crop(src, x, y, w, h, flipY) {
  const c = document.createElement('canvas');
  c.width = w; c.height = h;
  const g = c.getContext('2d');
  g.imageSmoothingEnabled = false;
  if (flipY) { g.translate(0, h); g.scale(1, -1); }
  g.drawImage(src, x, y, w, h, 0, 0, w, h);
  return c;
}

/** Коробка из 6 граней. size — в пикселях скина, U — CSS-пикселей на пиксель скина. */
function s3Box(tex, u, v, [w, h, d], U, scale = [1, 1, 1], texScale = 1) {
  const box = document.createElement('div');
  box.className = 's3-box';
  if (scale.some((k) => k !== 1)) box.style.transform = `scale3d(${scale.join(',')})`;
  const f = s3Faces(w, h, d);
  const place = {
    front: `translateZ(${d / 2 * U}px)`,
    back: `rotateY(180deg) translateZ(${d / 2 * U}px)`,
    right: `rotateY(-90deg) translateZ(${w / 2 * U}px)`,
    left: `rotateY(90deg) translateZ(${w / 2 * U}px)`,
    top: `rotateX(90deg) translateZ(${h / 2 * U}px)`,
    bottom: `rotateX(-90deg) translateZ(${h / 2 * U}px)`,
  };
  for (const [name, [fx, fy, fw, fh]] of Object.entries(f)) {
    const k = texScale;
    const face = s3Crop(tex, (u + fx) * k, (v + fy) * k, fw * k, fh * k, name === 'bottom');
    face.className = 's3-face';
    face.style.width = fw * U + 'px';
    face.style.height = fh * U + 'px';
    face.style.marginLeft = -fw * U / 2 + 'px';
    face.style.marginTop = -fh * U / 2 + 'px';
    face.style.transform = place[name];
    box.append(face);
  }
  return box;
}

/** Приводит скин к 64×64 (старые 64×32 — левые рука/нога зеркалом правых). Возвращает [canvas, k]. */
function s3Normalize(img) {
  const k = Math.max(1, Math.round(img.width / 64));
  if (img.height * 2 !== img.width) return [img, k];
  const c = document.createElement('canvas');
  c.width = 64 * k; c.height = 64 * k;
  const g = c.getContext('2d');
  g.imageSmoothingEnabled = false;
  g.drawImage(img, 0, 0);
  const mirror = (sx, sy, dx, dy) => {
    g.save(); g.translate((dx + 16) * k, dy * k); g.scale(-1, 1);
    g.drawImage(c, sx * k, sy * k, 16 * k, 16 * k, 0, 0, 16 * k, 16 * k); g.restore();
  };
  mirror(0, 16, 16, 48); mirror(40, 16, 32, 48);
  return [c, k];
}

/**
 * Создаёт просмотрщик в container. opts: { skin: canvas|img, cape: canvas|null, slim: bool }.
 * Возвращает { update(opts), destroy() }.
 */
function createSkinViewer(container, opts = {}) {
  const st = { yaw: -28, pitch: -8, zoom: 1, walk: true, overlay: true, cape: true, t0: performance.now(),
    drag: null, idleAt: performance.now(), raf: 0, parts: {}, opts: {} };
  container.classList.add('s3-wrap');
  container.innerHTML = `
    <div class="s3-scene"><div class="s3-root"></div></div>
    <div class="s3-bar">
      <button class="s3-btn on" data-s3="walk" title="Ходьба">${typeof ic === 'function' ? ic('play') : ''}<span>Ходьба</span></button>
      <button class="s3-btn on" data-s3="overlay" title="Второй слой (одежда)"><span>2-й слой</span></button>
      <button class="s3-btn on" data-s3="cape" title="Плащ"><span>Плащ</span></button>
      <button class="s3-btn" data-s3="reset" title="Повернуть лицом">${typeof ic === 'function' ? ic('refresh') : ''}</button>
    </div>`;
  if (typeof hydrateIcons === 'function') hydrateIcons(container);
  const scene = container.querySelector('.s3-scene');
  const root = container.querySelector('.s3-root');

  const build = () => {
    const o = st.opts;
    root.innerHTML = '';
    st.parts = {};
    if (!o.skin) return;
    const U = Math.max(4, Math.floor(Math.min(scene.clientHeight || 340, (scene.clientWidth || 260) * 1.6) / 40));
    st.U = U;
    const [tex, k] = s3Normalize(o.skin);
    for (const [name, p] of Object.entries(S3_PARTS)) {
      const size = p.arm && o.slim ? [3, 12, 4] : p.size;
      const pos = p.arm && o.slim ? [p.pos[0] - p.arm * 0.5, p.pos[1], p.pos[2]] : p.pos;
      const piv = document.createElement('div');
      piv.className = 's3-pivot';
      piv.style.setProperty('--px', `${p.pivot[0] * U}px`);
      piv.style.setProperty('--py', `${(p.pivot[1] - 16) * U}px`);
      piv.style.setProperty('--pz', `${p.pivot[2] * U}px`);
      const off = document.createElement('div');
      off.className = 's3-off';
      off.style.transform = `translate3d(${(pos[0] - p.pivot[0]) * U}px,${(pos[1] - p.pivot[1]) * U}px,${(pos[2] - p.pivot[2]) * U}px)`;
      off.append(s3Box(tex, p.uv[0], p.uv[1], size, U, [1, 1, 1], k));
      const over = s3Box(tex, p.o[0], p.o[1], size, U,
        size.map((v) => (v + p.grow * 2) / v), k);
      over.classList.add('s3-over');
      off.append(over);
      piv.append(off);
      root.append(piv);
      st.parts[name] = piv;
    }
    if (o.cape) {                                     // плащ 10×16×1, висит на спине
      const piv = document.createElement('div');
      piv.className = 's3-pivot s3-cape';
      piv.style.setProperty('--px', '0px');
      piv.style.setProperty('--py', `${(8 - 16) * U}px`);
      piv.style.setProperty('--pz', `${-2.6 * U}px`);
      const off = document.createElement('div');
      off.className = 's3-off';
      off.style.transform = `translate3d(0,${8 * U}px,${-0.5 * U}px) rotateY(180deg)`;
      const ck = Math.max(1, Math.round(o.cape.width / 64));
      off.append(s3Box(o.cape, 0, 0, [10, 16, 1], U, [1, 1, 1], ck));
      piv.append(off);
      root.append(piv);
      st.parts.cape = piv;
    }
    applyToggles();
  };

  const applyToggles = () => {
    root.classList.toggle('no-over', !st.overlay);
    if (st.parts.cape) st.parts.cape.style.display = st.cape ? '' : 'none';
    container.querySelectorAll('[data-s3]').forEach((b) => {
      if (b.dataset.s3 !== 'reset') b.classList.toggle('on', !!st[b.dataset.s3]);
    });
    const capeBtn = container.querySelector('[data-s3=cape]');
    if (capeBtn) capeBtn.hidden = !st.opts.cape;
  };

  const frame = (now) => {
    st.raf = 0;
    if (!container.isConnected) { ro.disconnect(); return; }
    if (document.body.classList.contains('ui-idle')) {        // простой — останавливаемся, uiWake запустит снова
      container.__s3wake = () => { container.__s3wake = null; if (!st.raf) st.raf = requestAnimationFrame(frame); };
      return;
    }
    if (!document.hidden && container.offsetParent !== null) {   // скрыт (режим рисования) — не тратим кадры
      const t = (now - st.t0) / 1000;
      if (!st.drag && now - st.idleAt > 4000) st.yaw += 0.12;      // без мыши медленно поворачивается сам
      root.style.transform = `scale(${st.zoom}) rotateX(${st.pitch}deg) rotateY(${st.yaw}deg)`;
      const sw = st.walk ? Math.sin(t * 4.2) * 32 : 0;
      const breathe = Math.sin(t * 1.6) * 2.5;
      const set = (name, rx, rz = 0) => {
        const p = st.parts[name];
        if (p) p.style.transform = `translate3d(var(--px),var(--py),var(--pz)) rotateX(${rx}deg) rotateZ(${rz}deg)`;
      };
      set('rarm', sw, -breathe - 2); set('larm', -sw, breathe + 2);
      set('rleg', -sw); set('lleg', sw);
      set('head', st.walk ? Math.sin(t * 8.4) * 2 : 0); set('body', 0);
      set('cape', 8 + (st.walk ? Math.abs(Math.sin(t * 4.2)) * 14 + 6 : Math.sin(t * 1.6) * 2));
    }
    st.raf = requestAnimationFrame(frame);
  };

  scene.addEventListener('pointerdown', (e) => {
    st.drag = { x: e.clientX, y: e.clientY, yaw: st.yaw, pitch: st.pitch };
    scene.setPointerCapture(e.pointerId);
    scene.classList.add('grab');
  });
  scene.addEventListener('pointermove', (e) => {
    if (!st.drag) return;
    st.yaw = st.drag.yaw + (e.clientX - st.drag.x) * 0.6;
    st.pitch = Math.max(-60, Math.min(60, st.drag.pitch - (e.clientY - st.drag.y) * 0.4));
  });
  const up = () => { st.drag = null; st.idleAt = performance.now(); scene.classList.remove('grab'); };
  scene.addEventListener('pointerup', up);
  scene.addEventListener('pointercancel', up);
  scene.addEventListener('wheel', (e) => {
    e.preventDefault();
    st.zoom = Math.max(0.6, Math.min(1.8, st.zoom * (e.deltaY > 0 ? 0.92 : 1.08)));
  }, { passive: false });
  container.querySelector('.s3-bar').addEventListener('click', (e) => {
    const b = e.target.closest('[data-s3]'); if (!b) return;
    const k = b.dataset.s3;
    if (k === 'reset') { st.yaw = 0; st.pitch = -6; st.zoom = 1; st.idleAt = performance.now(); return; }
    st[k] = !st[k];
    applyToggles();
  });

  let resizeT = 0;
  const ro = new ResizeObserver(() => { clearTimeout(resizeT); resizeT = setTimeout(build, 120); });
  ro.observe(scene);

  const api3 = {
    update(o) { st.opts = { ...st.opts, ...o }; build(); },
    destroy() { ro.disconnect(); cancelAnimationFrame(st.raf); container.innerHTML = ''; },
  };
  api3.update(opts);
  st.raf = requestAnimationFrame(frame);
  return api3;
}
