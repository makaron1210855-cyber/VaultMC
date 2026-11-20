/* Вкладка сборки «Свой сервер»: выделенный сервер той же версии и с теми же модами (без клиентских).
 * Сборка — backend/serverapi.py → core/serverpack.py, запуск — core/serverrun.py.
 */

const SRV_STATE = { off: 'выключен', starting: 'запускается…', running: 'работает', stopping: 'выключается…' };
let srvView = null;          // { body, inst } открытой вкладки — для событий

const srvCheckHtml = (attrs, on, label) =>
  `<label class="check"><input type="checkbox" ${attrs} ${on ? 'checked' : ''}><span class="box">${iconSvg('check')}</span>${label}</label>`;

function srvLogClass(line) {
  if (/\b(ERROR|FATAL|Exception)\b/.test(line)) return 'err';
  if (/\bWARN\b/.test(line)) return 'warn';
  if (/^(\[VaultMC\]|> )/.test(line)) return 'sys';
  return '';
}

function srvAppend(box, lines) {
  if (!box) return;
  const stick = box.scrollTop + box.clientHeight >= box.scrollHeight - 30;
  const frag = document.createDocumentFragment();
  for (const l of lines) { const d = document.createElement('div'); d.className = srvLogClass(l); d.textContent = l; frag.appendChild(d); }
  box.appendChild(frag);
  while (box.childElementCount > 400) box.firstElementChild.remove();
  if (stick) box.scrollTop = box.scrollHeight;
}

async function renderDedicated(body, inst) {
  srvView = { body, inst };
  body.innerHTML = '<div class="row muted"><span class="spinner"></span> Загрузка...</div>';
  const info = await safe(api.server_info(inst.id));
  if (!info || !body.isConnected) return;
  if (info.problem) {
    body.innerHTML = `<div class="empty"><b>Сервер для этой сборки не собрать</b><span>${esc(info.problem)}</span></div>`;
    return;
  }
  if (!info.built) return srvSetup(body, inst, info);
  srvPanel(body, inst, info);
}

/* ---------------------------------------------------------------- готовый сервер */
function srvPanel(body, inst, info) {
  const m = info.manifest, st = info.status, on = st.state !== 'off' && st.state !== 'busy';
  const port = st.port || m.port;
  const addrs = [`127.0.0.1:${port}`, ...info.addresses.map((a) => `${a}:${port}`)];
  body.innerHTML = `
    <div class="card srv-card">
      <div class="row wrap">
        <div class="grow"><div class="title">${ic('server')} Сервер «${esc(m.name || inst.name)}»
          <span class="chip ${st.state === 'running' ? 'accent' : on ? 'warn' : ''}" id="srvState">${esc(SRV_STATE[st.state] || 'выключен')}</span></div>
          <div class="small faint" style="margin-top:4px">${esc(m.loader)} ${esc(m.mc)} ${esc(m.loader_version || '')} · Java ${m.java_major} · ${m.ram_gb} ГБ · порт ${m.port} · модов: ${m.mods.length}</div></div>
        ${on ? `<button class="btn primary" data-a="play" ${st.state === 'running' ? '' : 'disabled'}>${ic('play')} Играть на нём</button>
          <button class="btn danger" data-a="stop" ${st.state === 'stopping' ? 'disabled' : ''}>${ic('square')} Остановить</button>`
          : `<button class="btn primary" data-a="start" ${st.state === 'busy' ? 'disabled title="Запущен сервер другой сборки"' : ''}>${ic('play')} Запустить</button>`}
      </div>
      ${st.state === 'busy' ? '<div class="note" style="margin-top:10px">Сейчас запущен сервер другой сборки — сначала остановите его там.</div>' : ''}
      ${info.eula ? '' : `<div class="note" style="margin-top:10px">${ic('alert')} Нет согласия с EULA Minecraft — без него сервер не запустится. Нажмите «Обновить сервер» и отметьте его.</div>`}
      <div class="small muted" style="margin-top:10px">Адрес для входа: ${addrs.map((a) => `<span class="chip mono srv-addr" data-copy="${esc(a)}" title="Скопировать">${esc(a)}</span>`).join(' ')}</div>
      <div class="small faint" style="margin-top:4px">127.0.0.1 — с этого ПК, остальные — для друзей в той же сети или в Radmin VPN. Из интернета — откройте порт ${m.port} на роутере или залейте сервер на VPS.</div>
    </div>
    <div class="spacer"></div>
    <div class="log srv-log" id="srvLog"></div>
    <div class="row" style="margin-top:8px"><input class="input grow mono" id="srvCmd" placeholder="Команда серверу, например: whitelist add Друг" ${on ? '' : 'disabled'}>
      <button class="btn" data-a="cmd" ${on ? '' : 'disabled'}>Отправить</button></div>
    <div class="spacer"></div>
    <div class="row wrap">
      <button class="btn" data-a="rebuild">${ic('refresh')} Обновить сервер</button>
      <button class="btn" data-a="folder">${ic('folder')} Папка</button>
      <button class="btn" data-a="export">${ic('upload')} Архив для VPS</button>
      <span class="grow"></span>
      <button class="btn danger" data-a="delete" ${on ? 'disabled' : ''}>${ic('trash')} Удалить сервер</button></div>
    <div class="small faint" style="margin-top:6px">«Обновить сервер» — после изменения модов сборки: меняются моды и их настройки, мир и списки игроков остаются.</div>`;
  hydrateIcons(body);
  srvAppend($('#srvLog', body), st.lines || []);
  const send = async () => {
    const box = $('#srvCmd', body); const t = box.value.trim(); if (!t) return;
    if (okOrToast(await safe(api.server_command(t)))) box.value = '';
  };
  body.onkeydown = (e) => { if (e.key === 'Enter' && e.target.id === 'srvCmd') send(); };
  body.onclick = async (e) => {
    const copy = e.target.closest('[data-copy]')?.dataset.copy;
    if (copy) { copyText(copy); return toast('Адрес скопирован'); }
    const a = e.target.closest('[data-a]')?.dataset.a; if (!a) return;
    if (a === 'start' && okOrToast(await safe(api.server_start(inst.id)))) renderDedicated(body, inst);
    if (a === 'stop') { await safe(api.server_stop()); }
    if (a === 'cmd') send();
    if (a === 'play') okOrToast(await safe(api.server_play(inst.id)));
    if (a === 'folder') okOrToast(await safe(api.server_open_folder(inst.id)));
    if (a === 'rebuild') srvSetup(body, inst, info);
    if (a === 'export') srvExport(inst, info);
    if (a === 'delete' && await confirmDialog('Удалить сервер?', 'Папка сервера будет удалена вместе с его миром, списком игроков и настройками. Сборка и её миры не изменятся.', 'Удалить', true)) {
      if (okOrToast(await safe(api.server_delete(inst.id)))) { toast('Сервер удалён'); renderDedicated(body, inst); }
    }
  };
}

async function srvExport(inst, info) {
  const m = openModal(`<h3>Архив для VPS</h3>
    <p class="muted">В архиве — всё для запуска на другом компьютере: start.bat для Windows, start.sh для Linux и README.txt с командами. На VPS нужна Java ${info.manifest.java_major}.</p>
    ${srvCheckHtml('id="srvWithWorld"', info.has_world, 'Положить и мир сервера')}
    <div class="foot"><button class="btn ghost" data-close>Отмена</button><button class="btn primary" data-go>${ic('upload')} Сохранить архив</button></div>`);
  m.querySelector('[data-go]').onclick = async () => {
    const withWorld = m.querySelector('#srvWithWorld').checked;
    closeModal();
    const r = await safe(api.server_export(inst.id, withWorld));
    if (r && okOrToast(r) && r.started) toast('Собираю архив сервера...');
  };
}

/* ---------------------------------------------------------------- сборка / обновление */
async function srvSetup(body, inst, info) {
  const rebuild = info.built;
  body.innerHTML = `<div class="row muted"><span class="spinner"></span> Смотрю, какие моды нужны серверу (по описанию модов и Modrinth)...</div>`;
  const plan = inst.loader === 'vanilla' ? { mods: [] } : await safe(api.server_plan(inst.id));
  if (!plan || !body.isConnected) return;
  // при обновлении — прежний выбор игрока; новые моды сборки — по предложению лаунчера
  const prev = new Set(rebuild ? plan.previous || [] : []), skipped = new Set(rebuild ? plan.skipped || [] : []);
  const mods = plan.mods.map((x) => ({ ...x, pick: prev.has(x.file) ? true : skipped.has(x.file) ? false : x.on_server }));
  const client = mods.filter((x) => !x.on_server).length;
  const nick = info.nick || '';
  body.innerHTML = `
    <div class="card">
      <div class="title">${ic('server')} ${rebuild ? 'Обновить сервер' : 'Сервер из этой сборки'}</div>
      <div class="small muted" style="margin-top:4px">Лаунчер соберёт выделенный сервер той же версии (${esc(inst.loaderLabel)} ${esc(inst.game_version)}) с модами сборки — кроме тех, что работают только у игрока.
        Его можно запустить здесь или залить на VPS. Друзья заходят на него по адресу, даже когда вы не в игре.</div>
      <div class="grid2" style="margin-top:12px">
        <label class="field"><span>Память серверу, ГБ</span><input class="input" id="srvRam" type="number" min="1" max="64" value="${info.ram}"></label>
        <label class="field"><span>Порт</span><input class="input" id="srvPort" type="number" min="1024" max="65535" value="${info.port}"></label>
        <label class="field"><span>Мир</span><select class="input" id="srvWorld">
          <option value="">${info.has_world ? 'Оставить мир сервера' : 'Новый мир'}</option>
          ${info.worlds.map((w) => `<option value="${esc(w)}">${info.has_world ? 'Заменить копией: ' : 'Копия мира: '}${esc(w)}</option>`).join('')}</select></label>
        <label class="field"><span>Оператор (все команды)</span><input class="input" id="srvOp" value="${esc(nick)}" placeholder="ваш ник"></label>
      </div>
      <div style="margin-top:10px">${srvCheckHtml('id="srvWl"', false, 'Пускать только по списку')}
        <input class="input" id="srvWlNicks" placeholder="Ники друзей через запятую" style="margin-top:6px;display:none"></div>
      <div class="small faint" style="margin-top:6px">Сервер пускает игроков без лицензии (online-mode=false). Без списка зайти может любой, кто знает адрес, — в том числе под чужим ником.</div>
      <div style="margin-top:10px">${srvCheckHtml('id="srvEula"', info.eula, `Я принимаю <a href="#" data-eula>EULA Minecraft</a>`)}</div>
    </div>
    ${mods.length ? `<div class="spacer"></div>
    <div class="row"><div class="title grow">Моды на сервере</div><span class="small muted" id="srvModCount"></span></div>
    <div class="small faint" style="margin:2px 0 8px">Сняты моды только для игрока (${client}): графика, интерфейс, карты. Если сервер ругается, что не хватает мода, — отметьте его и обновите сервер.</div>
    <div class="list srv-mods">${mods.map((x, n) => `
      <div class="item"><div class="grow"><div class="title">${esc(x.name)}</div>
        <div class="small faint">${esc(x.file)}${x.why ? ' · ' + esc(x.why) : ''}</div></div>
        ${srvCheckHtml(`data-n="${n}"`, x.pick, '')}</div>`).join('')}</div>` : ''}
    <div class="spacer"></div>
    <div class="row">${rebuild ? '<button class="btn ghost" data-a="back">Отмена</button>' : ''}<span class="grow"></span>
      <button class="btn primary" data-a="build">${ic('server')} ${rebuild ? 'Обновить сервер' : 'Собрать сервер'}</button></div>`;
  hydrateIcons(body);
  const count = () => { const c = $('#srvModCount', body); if (c) c.textContent = `${mods.filter((x) => x.pick).length} из ${mods.length}`; };
  count();
  body.onchange = (e) => {
    if (e.target.dataset.n !== undefined) { mods[+e.target.dataset.n].pick = e.target.checked; count(); }
    if (e.target.id === 'srvWl') $('#srvWlNicks', body).style.display = e.target.checked ? '' : 'none';
  };
  body.onkeydown = null;
  body.onclick = async (e) => {
    if (e.target.closest('[data-eula]')) { e.preventDefault(); api.open_url(info.eula_url); return; }
    const a = e.target.closest('[data-a]')?.dataset.a; if (!a) return;
    if (a === 'back') return renderDedicated(body, inst);
    if (a !== 'build') return;
    if (!$('#srvEula', body).checked) return toast('Без согласия с EULA Minecraft сервер не запустится', 'warn');
    const world = $('#srvWorld', body).value;
    if (world && info.has_world && !await confirmDialog('Заменить мир сервера?', `Мир сервера заменится копией «${world}». Прежний мир останется в папке сервера (world-old-…).`, 'Заменить')) return;
    const wl = $('#srvWl', body).checked ? $('#srvWlNicks', body).value.split(/[\s,;]+/).filter(Boolean) : [];
    const op = $('#srvOp', body).value.trim();
    if ($('#srvWl', body).checked && nick && !wl.includes(nick)) wl.push(nick);
    const opts = { mods: mods.filter((x) => x.pick).map((x) => x.file), world, replace_world: !!world && info.has_world,
      ram_gb: +$('#srvRam', body).value, port: +$('#srvPort', body).value, whitelist: wl, op, eula: true };
    const r = await safe(api.server_build(inst.id, opts));
    if (r && okOrToast(r) && r.started) {
      body.innerHTML = `<div class="row muted"><span class="spinner"></span> Собираю сервер — прогресс внизу. Forge и NeoForge ставятся 1–3 минуты.</div>`;
    }
  };
}

onEvent('server_built', (r) => {
  if (!r) return;
  toast(r.world_note === 'kept' ? 'Сервер обновлён, мир сервера оставлен прежним' : 'Сервер готов — нажмите «Запустить»');
  if (srvView && srvView.body.isConnected && srvView.inst.id === r.instance_id) renderDedicated(srvView.body, srvView.inst);
});
onEvent('error', () => { if (srvView && srvView.body.isConnected && srvView.body.querySelector('.spinner')) renderDedicated(srvView.body, srvView.inst); });
onEvent('server_log', (line) => {
  if (srvView && srvView.body.isConnected) srvAppend($('#srvLog', srvView.body), [String(line)]);
});
onEvent('server_state', (st) => {
  if (st.state === 'off' && st.exit_code && st.exit_code !== 0) toast(`Сервер остановился с ошибкой (код ${st.exit_code}) — смотрите журнал на вкладке «Свой сервер»`, 'warn', 8000);
  if (!srvView || !srvView.body.isConnected || srvView.inst.id !== st.instance) return;
  const keepLog = $('#srvLog', srvView.body);
  if (keepLog) renderDedicated(srvView.body, srvView.inst);
});
onEvent('server_exported', (r) => {
  if (r.error) toast(r.error, 'error', 7000);
  else toast(`Архив сервера сохранён: ${r.path}`, 'info', 7000);
});
