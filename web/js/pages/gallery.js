/* Общая галерея скриншотов всех сборок и статистика: время в игре и «последний раз играл» по сборкам. */

const GALLERY_LIMIT = 60;

async function openGallery() {
  const m = openModal(`<h3>Скриншоты</h3><div class="row muted"><span class="spinner"></span> Загрузка...</div>`, { wide: true });
  const shots = await safe(api.all_screenshots(GALLERY_LIMIT)) || [];
  if (!m.isConnected) return;
  m.innerHTML = `<button class="btn ghost sm icon x" data-close>${ic('x')}</button>
    <h3>Скриншоты</h3>
    ${shots.length ? `<div class="small muted" style="margin-bottom:10px">Последние ${shots.length} из всех сборок. В игре — клавиша F2.</div>
      <div class="shots">${shots.map((s, n) => `<button class="shot" data-n="${n}" title="${esc(s.name)} · ${esc(s.file)}"><img alt=""><span>${esc(s.name)} · ${fmtWhen(s.date)}</span></button>`).join('')}</div>`
      : '<div class="empty"><b>Скриншотов пока нет</b><span>Нажмите F2 в игре — снимок появится здесь.</span></div>'}
    <div class="foot"><button class="btn" data-close>Закрыть</button></div>`;
  m.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
  const io = new IntersectionObserver((entries) => entries.forEach(async (en) => {
    if (!en.isIntersecting) return;
    io.unobserve(en.target);
    const s = shots[+en.target.dataset.n];
    $('img', en.target).src = await api.screenshot(s.inst, s.file).catch(() => '') || '';
  }), { root: m, rootMargin: '200px' });
  $$('.shot', m).forEach((el) => io.observe(el));
  m.addEventListener('click', (e) => {
    const shot = e.target.closest('.shot'); if (!shot) return;
    openShotViewer(m, { id: '' }, shots, +shot.dataset.n, () => {});
  });
}

function openPlayStats() {
  const list = [...S.instances].sort((a, b) => (b.play_seconds || 0) - (a.play_seconds || 0));
  const total = list.reduce((n, i) => n + (i.play_seconds || 0), 0);
  const max = Math.max(1, ...list.map((i) => i.play_seconds || 0));
  const fmt = (sec) => { const h = Math.floor(sec / 3600), mi = Math.floor((sec % 3600) / 60); return h ? `${h} ч ${mi} мин` : `${mi} мин`; };
  const m = openModal(`<h3>Время в игре</h3>
    <div class="small muted" style="margin-bottom:12px">Всего: <b>${fmt(total)}</b>, сборок: ${list.length}</div>
    ${list.length ? list.map((i) => `<div class="row" style="gap:10px;margin-bottom:8px;align-items:center">
        <div class="grow" style="min-width:0"><b>${esc(i.name)}</b>
          <div class="small muted">${i.last_played ? `Последний раз: ${fmtWhen(i.last_played)}` : 'Ещё не запускали'}</div>
          <div style="height:4px;background:rgba(var(--ov),.12);border-radius:2px;margin-top:4px"><div style="height:4px;width:${Math.round(100 * (i.play_seconds || 0) / max)}%;background:${esc(i.color)};border-radius:2px"></div></div></div>
        <span class="chip">${ic('clock')} ${fmt(i.play_seconds || 0)}</span></div>`).join('')
      : '<div class="empty"><b>Сборок пока нет</b></div>'}
    <div class="foot"><button class="btn" data-close>Закрыть</button></div>`);
  hydrateIcons(m);
  m.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
}
