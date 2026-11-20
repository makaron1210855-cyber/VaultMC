/* Вкладки сборки: «Миры», «Серверы», «Скриншоты». */

const fmtSize = (b) => (b >= 1 << 30 ? (b / (1 << 30)).toFixed(1) + ' ГБ' : b >= 1 << 20 ? (b / (1 << 20)).toFixed(1) + ' МБ' : Math.max(1, Math.round(b / 1024)) + ' КБ');
const fmtWhen = (sec) => {
  const d = new Date(sec * 1000), diff = (Date.now() - d) / 1000;
  if (diff < 3600) return 'только что';
  if (diff < 86400) return `${Math.floor(diff / 3600)} ч назад`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} дн назад`;
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' });
};

/* ================================================================= миры */
async function renderWorlds(body, inst) {
  body.innerHTML = '<div class="row muted"><span class="spinner"></span> Загрузка...</div>';
  const res = await safe(api.worlds(inst.id));
  if (!res || !body.isConnected) return;
  const others = S.instances.filter((i) => i.id !== inst.id);
  const cloud = res.cloud || { supported: false, enabled: false, worlds: {}, cloud_only: [] };
  const showCloud = cloud.supported && (cloud.enabled || Object.keys(cloud.worlds).length);
  if (showCloud && !body.__cloudAsked) { body.__cloudAsked = true; safe(api.cloud_refresh()); }   // свежий список облака — событием cloud_update
  body.innerHTML = `
    <div class="row" style="margin-bottom:10px"><div class="small muted grow">Миров: ${res.worlds.length}. Копии хранятся в папке сборки и не мешают игре.
      ${res.auto ? 'После игры лаунчер сам делает копию миров, в которых вы играли (последние 5 на мир).' : 'Автокопии после игры выключены в настройках.'}
      ${res.running ? '<br><b>Игра запущена</b> — копию сделать можно после выхода: открытый мир скопировался бы не целиком.' : ''}</div>
      <button class="btn sm ghost" data-a="folder">${ic('folder')} Папка миров</button>
      <button class="btn sm ghost" data-a="bfolder">${ic('archive')} Папка копий</button></div>
    <div class="list">${res.worlds.map((w) => `
      <div class="item world" data-w="${esc(w.folder)}">
        ${w.icon ? `<img class="world-ic" src="${w.icon}" alt="">` : `<div class="world-ic ph">${ic('world')}</div>`}
        <div class="grow"><div class="title">${esc(w.name)}</div>
          <div class="small faint">${[w.mode, w.version, fmtWhen(w.lastPlayed), fmtSize(w.size)].filter(Boolean).map(esc).join(' · ')}</div>
          ${showCloud ? cloudRowHtml(cloud.worlds[w.folder]) : ''}</div>
        ${cloud.supported && cloud.enabled ? `<button class="btn sm ghost" data-c="toggle" data-on="${cloud.worlds[w.folder] && cloud.worlds[w.folder].status !== 'off' ? 0 : 1}" title="Хранить мир в облаке VaultMC">${ic('cloud')} ${cloud.worlds[w.folder] && cloud.worlds[w.folder].status !== 'off' ? 'Из облака' : 'В облако'}</button>` : ''}
        <button class="btn sm" data-a="backup" title="Сделать резервную копию" ${res.running ? 'disabled' : ''}>${ic('archive')} Копия</button>
        ${others.length ? `<button class="btn sm ghost" data-a="move" title="Скопировать в другую сборку">${ic('copy')}</button>` : ''}
        <button class="btn sm danger" data-a="del" title="Удалить мир">${ic('trash')}</button></div>`).join('')
      || '<div class="empty"><b>Миров пока нет</b><span>Создайте мир в игре — он появится здесь.</span></div>'}</div>
    ${showCloud ? cloudOnlyHtml(cloud.cloud_only) : ''}
    ${res.backups.length ? `<div class="spacer"></div><div class="h2">Резервные копии</div>
      <div class="list">${res.backups.map((b) => `
        <div class="item" data-b="${esc(b.file)}"><div class="grow"><div class="title">${esc(b.file.replace(/\.zip$/, '').replace(' auto ', ' '))}
            ${b.auto ? '<span class="chip">автокопия</span>' : ''}</div>
          <div class="small faint">${fmtWhen(b.date)} · ${fmtSize(b.size)}</div></div>
          <button class="btn sm" data-a="restore">${ic('refresh')} Восстановить</button>
          <button class="btn sm danger" data-a="delb">${ic('trash')}</button></div>`).join('')}</div>` : ''}
    ${res.trash.length ? `<div class="spacer"></div><div class="h2">Корзина</div>
      <div class="small muted" style="margin-bottom:8px">Удалённые миры хранятся здесь 7 дней, потом стираются насовсем.</div>
      <div class="list">${res.trash.map((t) => `
        <div class="item" data-t="${esc(t.entry)}"><div class="grow"><div class="title">${esc(t.name)}</div>
          <div class="small faint">${t.reason === 'cloud' ? 'заменён версией из облака' : 'удалён'} ${fmtWhen(t.date)} · ${fmtSize(t.size)} · сотрётся через ${t.left_days} дн.</div></div>
          <button class="btn sm" data-a="untrash">${ic('refresh')} Вернуть</button>
          <button class="btn sm danger" data-a="purge" title="Стереть насовсем">${ic('trash')}</button></div>`).join('')}</div>` : ''}`;
  hydrateIcons(body);
  body.onclick = async (e) => {
    if (await cloudHandleClick(e, inst, body, () => renderWorlds(body, inst))) return;
    const a = e.target.closest('[data-a]')?.dataset.a; if (!a) return;
    const w = e.target.closest('[data-w]')?.dataset.w;
    const b = e.target.closest('[data-b]')?.dataset.b;
    const t = e.target.closest('[data-t]')?.dataset.t;
    const act = async (...args) => { const r = await safe(api.world_action(inst.id, ...args)); if (r && r.error) toast(r.error, 'error'); return r && !r.error ? r : null; };
    if (a === 'folder') return api.open_folder('saves', inst.id);
    if (a === 'bfolder') return api.open_folder('backups', inst.id);
    if (a === 'backup' && await act('backup', w)) toast('Делаю резервную копию...');
    if (a === 'del' && await confirmDialog('Удалить мир?', `«${w}» попадёт в корзину: 7 дней его можно вернуть здесь же, потом он сотрётся насовсем.`, 'Удалить', true)) {
      if (await act('delete', w)) { toast('Мир в корзине — вернуть можно внизу страницы'); renderWorlds(body, inst); }
    }
    if (a === 'untrash') {
      const r = await act('trash_restore', t);
      if (r) { toast(`Мир возвращён: «${r.folder}»`); renderWorlds(body, inst); }
    }
    if (a === 'purge' && await confirmDialog('Стереть мир насовсем?', 'Вернуть его будет нельзя.', 'Стереть', true)) {
      if (await act('trash_delete', t)) renderWorlds(body, inst);
    }
    if (a === 'restore') {
      const r = await act('restore', b);
      if (r) { toast(`Восстановлено как «${r.folder}» — текущий мир не тронут`); renderWorlds(body, inst); }
    }
    if (a === 'delb' && await confirmDialog('Удалить копию?', b, 'Удалить', true)) { if (await act('delete_backup', b)) renderWorlds(body, inst); }
    if (a === 'move') {
      const m = openModal(`<h3>Скопировать мир в другую сборку</h3><p class="muted small">«${esc(w)}» появится в выбранной сборке. Здесь мир тоже останется.</p>
        <div class="list">${others.map((o) => `<div class="item" data-to="${esc(o.id)}" style="cursor:pointer">
          <div class="grow"><div class="title">${esc(o.name)}</div><div class="small faint">${esc(o.loaderLabel || '')} ${esc(o.game_version)}</div></div>${ic('copy')}</div>`).join('')}</div>`);
      m.onclick = async (ev) => {
        const to = ev.target.closest('[data-to]')?.dataset.to; if (!to) return;
        const r = await act('copy', w, to);
        if (r) { m.close(); toast(`Мир скопирован в «${r.target}»`); }
      };
    }
  };
}
onEvent('world_backup_done', (r) => {
  toast(`Копия сохранена: ${r.file}`);
  if (S.page === 'instance') refreshPage();
});
onEvent('world_autobackup', () => {
  if (S.page === 'instance' && currentPageEl && currentPageEl.__tab === 'worlds') refreshPage();
});

/* ================================================================= серверы */
async function renderServers(body, inst) {
  let list = await safe(api.servers(inst.id)) || [];
  if (!body.isConnected) return;
  const draw = () => {
    body.innerHTML = `
      <div class="row" style="margin-bottom:10px">
        <input class="input" id="srvName" placeholder="Название (необязательно)" style="max-width:220px">
        <input class="input grow" id="srvIp" placeholder="Адрес сервера: mc.example.ru или 1.2.3.4:25565">
        <button class="btn primary" data-a="add">${ic('plus')} Добавить</button></div>
      <div class="small muted" style="margin:-2px 0 10px">Это тот же список, что в игре в «Сетевой игре». Серверы без лицензии работают с оффлайн-аккаунтом, с Ely.by — сервера с поддержкой Ely.by.</div>
      <div class="list" id="srvList">${list.map((s, n) => `
        <div class="item server" data-n="${n}">
          <div class="srv-ic">${s.icon ? `<img src="data:image/png;base64,${esc(s.icon)}" alt="">` : ic('server')}</div>
          <div class="grow"><div class="title">${esc(s.name)} <span class="small faint mono">${esc(s.ip)}</span></div>
            <div class="small faint srv-motd">проверяю...</div></div>
          <span class="chip srv-ping">…</span>
          <button class="btn sm primary" data-a="join">${ic('play')} Играть</button>
          <button class="btn sm danger" data-a="del">${ic('trash')}</button></div>`).join('')
        || '<div class="empty"><b>Серверов нет</b><span>Добавьте адрес — лаунчер покажет онлайн и пинг, а «Играть» сразу подключит к серверу.</span></div>'}</div>`;
    hydrateIcons(body);
    list.forEach((s, n) => pingRow(body, n, s.ip));
  };
  const save = async () => { list = await safe(api.save_servers(inst.id, list)) || list; draw(); };
  draw();
  body.onclick = async (e) => {
    const a = e.target.closest('[data-a]')?.dataset.a; if (!a) return;
    const n = +e.target.closest('[data-n]')?.dataset.n;
    if (a === 'add') {
      const ip = $('#srvIp', body).value.trim();
      if (!ip) return toast('Введите адрес сервера', 'warn');
      list.push({ name: $('#srvName', body).value.trim() || ip, ip }); save();
    }
    if (a === 'del') { list.splice(n, 1); save(); }
    if (a === 'join') { selectInstance(inst.id); play(list[n].ip); }
  };
  body.onkeydown = (e) => { if (e.key === 'Enter' && e.target.id === 'srvIp') $('[data-a=add]', body).click(); };
}

async function pingRow(body, n, ip) {
  const r = await api.server_ping(ip).catch(() => null);
  const row = $(`.server[data-n="${n}"]`, body); if (!row) return;
  const motd = $('.srv-motd', row), chip = $('.srv-ping', row);
  if (!r || !r.online) {
    motd.textContent = r ? `Сервер ${r.error}` : 'Сервер недоступен'; chip.textContent = 'офлайн'; chip.className = 'chip srv-ping'; return;
  }
  motd.textContent = [`${r.players} / ${r.max} игроков`, r.version, r.motd].filter(Boolean).join(' · ');
  chip.textContent = `${r.ping} мс`;
  chip.className = `chip srv-ping ${r.ping < 80 ? 'accent' : r.ping < 180 ? 'warn' : 'pink'}`;
  if (r.favicon) $('.srv-ic', row).innerHTML = `<img src="${r.favicon}" alt="">`;
}

/* ================================================================= скриншоты */
async function renderShots(body, inst) {
  const shots = await safe(api.screenshots(inst.id)) || [];
  if (!body.isConnected) return;
  body.innerHTML = `
    <div class="row" style="margin-bottom:10px"><div class="small muted grow">Скриншотов: ${shots.length}. В игре — клавиша F2.</div>
      <button class="btn sm ghost" data-a="folder">${ic('folder')} Открыть папку</button></div>
    ${shots.length ? `<div class="shots">${shots.map((s) => `<button class="shot" data-f="${esc(s.file)}" title="${esc(s.file)}"><img alt=""><span>${fmtWhen(s.date)}</span></button>`).join('')}</div>`
      : '<div class="empty"><b>Скриншотов пока нет</b><span>Нажмите F2 в игре — снимок появится здесь.</span></div>'}`;
  hydrateIcons(body);
  // картинки грузим, только когда они видны
  const io = new IntersectionObserver((entries) => entries.forEach(async (en) => {
    if (!en.isIntersecting) return;
    io.unobserve(en.target);
    const img = $('img', en.target);
    img.src = await api.screenshot(inst.id, en.target.dataset.f).catch(() => '') || '';
  }), { root: null, rootMargin: '200px' });
  $$('.shot', body).forEach((el) => io.observe(el));
  body.onclick = (e) => {
    if (e.target.closest('[data-a=folder]')) return api.open_folder('screenshots', inst.id);
    const shot = e.target.closest('.shot'); if (!shot) return;
    openShotViewer(body, inst, shots, shots.findIndex((x) => x.file === shot.dataset.f));
  };
}

/* просмотр скриншотов: стрелки ← → (и на клавиатуре), сохранить, удалить */
function openShotViewer(body, inst, shots, start, refresh) {
  const iid = (n) => shots[n].inst || inst.id;      // в общей галерее у каждого снимка своя сборка
  const again = refresh || (() => renderShots(body, inst));
  let i = Math.max(0, start);
  const cache = {};
  const load = async (n) => cache[iid(n) + shots[n].file] || (cache[iid(n) + shots[n].file] = await api.screenshot(iid(n), shots[n].file).catch(() => ''));
  const m = openModal(`<div class="shot-view">
      <img class="shot-full" alt="">
      <button class="shot-nav prev" data-nav="-1" title="Предыдущий (←)">${ic('chevronLeft')}</button>
      <button class="shot-nav next" data-nav="1" title="Следующий (→)">${ic('chevronRight')}</button></div>
    <div class="foot"><span class="small muted grow" id="shotName"></span>
      <button class="btn" data-save>${ic('download')} Сохранить</button>
      <button class="btn danger" data-del>${ic('trash')} Удалить</button><button class="btn" data-close>Закрыть</button></div>`, { wide: true });
  const show = async () => {
    const cur = shots[i];
    $('#shotName', m).textContent = `${cur.file} · ${i + 1} из ${shots.length}`;
    $('.prev', m).disabled = i === 0; $('.next', m).disabled = i === shots.length - 1;
    const src = await load(i);
    if (shots[i] === cur) $('.shot-full', m).src = src;
    if (i + 1 < shots.length) load(i + 1);               // заранее грузим следующий
  };
  const step = (d) => { const n = i + d; if (n >= 0 && n < shots.length) { i = n; show(); } };
  m.addEventListener('click', async (e) => {
    const nav = e.target.closest('[data-nav]');
    if (nav) return step(+nav.dataset.nav);
    if (e.target.closest('[data-save]')) {
      const r = await safe(api.save_screenshot(iid(i), shots[i].file));
      if (r && r.ok) toast('Скриншот сохранён'); else if (r && r.error) toast(r.error, 'error');
    }
    if (e.target.closest('[data-del]')) {
      const file = shots[i].file;
      if (!await confirmDialog('Удалить скриншот?', file, 'Удалить', true)) return openShotViewer(body, inst, shots, i, refresh);
      await safe(api.delete_screenshot(iid(i), file));
      shots.splice(i, 1);
      again();
      if (shots.length) openShotViewer(body, inst, shots, Math.min(i, shots.length - 1), refresh);
    }
  });
  const onKey = (e) => {
    if (!m.isConnected) return document.removeEventListener('keydown', onKey);
    if (e.key === 'ArrowLeft') step(-1);
    if (e.key === 'ArrowRight') step(1);
  };
  document.addEventListener('keydown', onKey);
  show();
}
