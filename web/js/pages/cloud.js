/* Облако миров (бета): значки у миров, окна выбора копии и предупреждений, карточка в «Настройках».
   Порядок действий — core/worldengine.py; интерфейс только показывает состояние и спрашивает игрока. */

const CLOUD_SIZE = (b) => (b >= 1 << 30 ? (b / (1 << 30)).toFixed(1) + ' ГБ' : b >= 1 << 20 ? Math.round(b / (1 << 20)) + ' МБ' : Math.max(1, Math.round(b / 1024)) + ' КБ');
const cloudDate = (ms) => (ms ? new Date(ms).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }) : '—');

/** Значок состояния мира: [текст, класс чипа]. */
function cloudBadgeText(b) {
  const dev = b.device_name ? `«${b.device_name}»` : '';
  switch (b.status) {
    case 'ok': return ['В облаке', 'accent'];
    case 'uploading': return [`Отправляется… ${b.pct ?? 0} %`, 'accent'];
    case 'downloading': return [`Скачивается… ${b.pct ?? 0} %`, 'accent'];
    case 'working': return ['Идёт работа с облаком…', 'accent'];
    case 'pending': return ['Ждёт отправки', ''];
    case 'busy': return [`Ждёт: ПК ${dev} отправляет этот мир`, 'warn'];
    case 'newer': return [`Новая версия с ПК ${dev}`, 'accent'];
    case 'choose': return ['Выберите копию', 'warn'];
    case 'later': return ['Выбор отложен', 'warn'];
    case 'crash': return ['Не отправлен после вылета', 'warn'];
    case 'leased': return [`Открыт на ПК ${dev}`, 'warn'];
    case 'gone': return ['Облачной копии нет', 'warn'];
    case 'missing_local': return ['Есть в облаке, на этом ПК папки нет', 'warn'];
    case 'offline': return ['Нет связи с облаком', ''];
    case 'paused': return ['Облако на паузе', 'warn'];
    case 'error': return [cloudErrorText(b.err, b.data), 'pink'];
    default: return ['', ''];
  }
}

function cloudErrorText(code, data = {}) {
  if (code === 'too_big') {
    const big = (data.parts || []).map(([n, s]) => `${n} — ${CLOUD_SIZE(s)}`).join(', ');
    return `Мир слишком большой для облака: ${CLOUD_SIZE(data.size || 0)} в архиве, можно до ${CLOUD_SIZE(data.max || 0)}.${big ? ' Больше всего места занимают: ' + big : ''}`;
  }
  if (code === 'quota') return 'В облаке не хватает места. Удалите из облака ненужный мир.';
  if (code === 'full') return 'Облако VaultMC сейчас заполнено. Мир остался на этом компьютере — попробуйте позже.';
  if (code === 'missing' || code === 'corrupt') return 'Облачная копия мира повреждена. Мир на этом ПК цел.';
  if (code === 'broken') return 'Мир повреждён: level.dat не читается — в облако не отправляется.';
  if (code === 'disk') return 'Не хватает места на диске.';
  return 'Облако: ошибка';
}

function cloudPausedReason(b) {
  if (b.reason === 'owner') return `Ник «${b.nick}» теперь другой аккаунт VaultMC (ник сменили на другом ПК или его занял другой игрок). Мир на этом ПК цел.`;
  if (b.reason === 'taken') return `Ник «${b.nick}» занят другим аккаунтом. Войдите заново. Мир на этом ПК цел.`;
  if (b.reason === 'account') return `Аккаунт «${b.nick}» удалён из лаунчера — облако для этого мира на паузе.`;
  return 'Сервер сейчас не поддерживает облако — привязки целы, ничего не меняется.';
}

/** Кнопка/действие по состоянию мира. */
function cloudActionHtml(b) {
  if (b.status === 'choose' || b.status === 'later') return `<button class="btn sm primary" data-c="choose">Выбрать копию</button>`;
  if (b.status === 'crash' || (b.status === 'error' && b.err !== 'corrupt' && b.err !== 'missing')) return `<button class="btn sm" data-c="send">Отправить</button>`;
  if (b.status === 'gone' || b.err === 'corrupt' || b.err === 'missing') return `<button class="btn sm" data-c="reupload">Загрузить заново</button>`;
  if (b.status === 'missing_local') return `<button class="btn sm" data-c="redownload">Скачать</button>`;
  return '';
}

/* ---------------------------------------------------------------- окно выбора копии (7.5) */
async function openCloudChoose(instanceId, folder, onDone) {
  const info = await safe(api.cloud_choose_info(instanceId, folder));
  if (!info || info.error) { if (info?.error) toast(info.error, 'error'); return; }
  const l = info.local, c = info.cloud;
  const newer = c.rev_ahead > 0 ? `<p class="small muted">В облаке на ${c.rev_ahead} ${c.rev_ahead === 1 ? 'отправку' : 'отправок'} новее, чем при прошлой синхронизации этого ПК.</p>` : '';
  const m = openModal(`<h3>Мир «${esc(info.title)}»: выберите копию</h3>
    <p class="muted small">Мир изменился в двух местах. На этом ПК и в облаке разные версии, и ни одна не сделана из другой — лаунчер не будет выбирать сам по часам компьютеров.</p>
    <div class="row" style="gap:10px;align-items:stretch">
      <div class="card grow"><div class="card-title">Этот ПК</div><div class="small muted">Последняя игра: ${cloudDate(l.last_played)} (по часам этого ПК)<br>${CLOUD_SIZE(l.size)} · ${esc(l.mc || '')}</div></div>
      <div class="card grow"><div class="card-title">Облако</div><div class="small muted">С ПК «${esc(c.device_name)}», отправлено ${cloudDate(c.at)} (по часам сервера)<br>${CLOUD_SIZE(c.size)} · ${esc(c.mc || '')}</div></div></div>
    ${newer}
    <div class="list" style="margin-top:8px">
      <div class="item"><div class="grow"><b>Оставить эту</b><div class="small muted">Копия на этом ПК станет новой версией в облаке, облачная останется прошлой на 3 дня.</div></div><button class="btn sm" data-x="keep_mine">Оставить эту</button></div>
      <div class="item"><div class="grow"><b>Взять из облака</b><div class="small muted">Папка этого ПК уйдёт в корзину сборки на 7 дней.</div></div><button class="btn sm" data-x="take_cloud">Взять из облака</button></div>
      <div class="item"><div class="grow"><b>Оставить обе</b><div class="small muted">Эта копия станет «${esc(folder)} (${esc(info.device)})», облачная скачается в «${esc(folder)}».</div></div><button class="btn sm" data-x="keep_both">Оставить обе</button></div></div>
    <p class="small faint">Заменённая копия не пропадёт: на ПК она 7 дней лежит в корзине сборки, в облаке прошлая версия хранится 3 дня.</p>
    <div class="foot"><button class="btn ghost" data-x="later">Решить потом</button></div>`, { wide: true });
  m.onclick = async (e) => {
    const x = e.target.closest('[data-x]')?.dataset.x; if (!x) return;
    const r = await safe(api.cloud_resolve(instanceId, folder, x));
    if (r && r.error) return toast(r.error, 'error');
    m.close();
    if (x !== 'later') toast('Готово — лаунчер работает с облаком в фоне');
    if (onDone) onDone(x);
  };
}

/* ---------------------------------------------------------------- перед «Играть» */
onEvent('cloud_choose', (r) => { if (S.page === 'instance' && r.instance) refreshPage(); });
onEvent('cloud_update', () => { if (S.page === 'instance' && currentPageEl && currentPageEl.__tab === 'worlds') refreshPage(); });
onEvent('cloud_progress', (r) => {
  const el = document.querySelector(`[data-w="${CSS.escape(r.folder)}"] .cloud-badge`);
  if (!el) return;
  const [t] = cloudBadgeText({ status: r.kind === 'down' ? 'downloading' : 'uploading', pct: r.pct });
  el.textContent = t;
});
onEvent('cloud_notice', (r) => {
  if (r.code === 'offline') toast('Нет связи с облаком — играете в копию с этого ПК. Если на другом ПК играли позже, после игры лаунчер попросит выбрать копию', 'warn', 7000);
  else if (r.code === 'corrupt' || r.code === 'missing') toast(`Облачная копия мира «${r.title || ''}» повреждена. Мир на этом ПК цел`, 'error', 7000);
  else if (r.code === 'disk') toast('Не хватает места на диске для мира из облака', 'error', 7000);
  else toast(`Мир «${r.title || ''}» из облака не обновлён`, 'warn', 6000);
});

onEvent('cloud_gate', (g) => {
  const address = g.address || '';
  if (g.kind === 'choose') return chooseOneByOne(g, g.folders.slice(), address);
  const lines = [];
  (g.lease || []).forEach((w) => lines.push(`<div class="crash-item"><div class="grow"><b>Мир «${esc(w.title)}» сейчас открыт на ПК «${esc(w.device_name)}»</b>
    <p class="small muted">Свежие изменения пока там. Если играть здесь, потом придётся выбирать копию.</p></div></div>`));
  (g.warn || []).forEach((w) => w.items.forEach((it) => {
    const text = it.code === 'version'
      ? `Мир сохранён в Minecraft ${it.cloud}, а сборка — ${it.here}. В старой версии мир может сломаться.`
      : `В этой сборке${it.missing.length ? ' нет модов, которые были на другом ПК: ' + it.missing.join(', ') : ' другой загрузчик'}. Блоки и предметы этих модов могут пропасть из мира — а потом эта версия уйдёт в облако.`;
    lines.push(`<div class="crash-item"><div class="grow"><b>Мир «${esc(w.title)}»</b><p class="small muted">${esc(text)}</p></div></div>`);
  }));
  const m = openModal(`<div class="crash-head">${ic('alert')}<div><h3>Прежде чем играть</h3></div></div><div class="crash-list">${lines.join('')}</div>
    <div class="foot"><button class="btn ghost" data-x="no">Не запускать</button><button class="btn" data-x="skip">Играть без обновления</button><button class="btn primary" data-x="go">Играть всё равно</button></div>`);
  m.onclick = (e) => {
    const x = e.target.closest('[data-x]')?.dataset.x; if (!x) return;
    m.close();
    if (x !== 'no') play(address, false, x);
  };
});

function chooseOneByOne(g, left, address) {
  if (!left.length) return play(address);
  const folder = left.shift();
  const skip = () => play(address, false, 'skip');
  openCloudChoose(g.instance, folder, (x) => (x === 'later' ? skip() : chooseOneByOne(g, left, address)));
}

/* ---------------------------------------------------------------- «Настройки»: карточка облака */
async function cloudSettingsCard(el) {
  const host = $('#cloudCard', el); if (!host) return;
  const st = await safe(api.cloud_status());
  if (!st || !st.supported || !host.isConnected) { host.innerHTML = ''; return; }   // сервер без облака — раздел прячем
  host.innerHTML = `<div class="card"><div class="card-title">${ic('cloud')} Облако миров (бета)</div>
    <p class="card-sub">Копия вашего мира лежит на сервере VaultMC. Играете дома, потом на другом компьютере — лаунчер сам скачает свежий мир, а после игры отправит изменения.</p>
    <ol class="small muted" style="margin:0 0 12px;padding-left:20px;line-height:1.6">
      <li>Включите переключатель ниже.</li>
      <li>Откройте сборку → вкладка «Миры» → у нужного мира нажмите «В облако».</li>
      <li>На другом ПК войдите в тот же ник и включите облако — мир появится на вкладке «Миры» в блоке «Только в облаке».</li>
    </ol>
    <label class="switch"><input type="checkbox" id="cloudOn" ${st.enabled ? 'checked' : ''}><span class="track"></span><span class="lbl"><b>Хранить миры в облаке VaultMC</b><small>Миры на компьютере облако не удаляет никогда</small></span></label>
    <div class="row" style="margin-top:8px"><label class="field"><span>Название этого компьютера</span><input class="input" id="cloudDev" maxlength="40" value="${esc(st.device)}" style="width:260px"></label></div></div>`;
  hydrateIcons(host);
  const on = $('#cloudOn', host), dev = $('#cloudDev', host);
  on.onchange = async () => {
    if (on.checked) {
      const ok = await confirmDialog('Облако миров (бета)',
        `Мир хранится на сервере VaultMC (Cloudflare, хранилище зашифровано). Скачать его можете только вы — по нику и паролю или через аккаунт Ely.by. До ${CLOUD_SIZE(st.limits.max_world)} на мир, 1 ГБ на игрока. Если 90 дней не заходить в VaultMC, облачная копия удаляется. Миры на компьютере облако не удаляет никогда.`,
        'Включить');
      if (!ok) { on.checked = false; return; }
    }
    const r = await safe(api.cloud_set(on.checked, dev.value));
    if (r) toast(on.checked ? 'Облако миров включено' : 'Облако миров выключено', 'info', 2000);
  };
  dev.onchange = () => safe(api.cloud_set(on.checked, dev.value));
}

/* ---------------------------------------------------------------- «Миры»: строки и действия */
function cloudRowHtml(b) {
  if (!b) return '';
  const [text, cls] = cloudBadgeText(b);
  if (b.status === 'off') return '';
  const reason = b.status === 'paused' ? `<div class="small faint">${esc(cloudPausedReason(b))}</div>` : '';
  const canPrev = b.prev && ['ok', 'newer'].includes(b.status);
  const prev = canPrev ? ` <button class="btn sm ghost" data-c="prevlocal" data-pn="${esc(b.nick)}" data-pw="${esc(b.wid)}" data-pat="${b.prev.at}" data-pdev="${esc(b.prev.device_name)}" data-psz="${b.prev.size}" title="Вернуть в облаке прошлую версию мира">${ic('refresh')} Прошлая версия</button>` : '';
  return `<div class="small"><span class="chip cloud-badge ${cls}">${esc(text)}</span> ${cloudActionHtml(b)}${prev}</div>${reason}`;
}

function cloudOnlyHtml(list) {
  if (!list.length) return '';
  return `<div class="spacer"></div><div class="h2">В облаке, не на этом ПК</div>
    <div class="list">${list.map((w) => `<div class="item" data-cw="${esc(w.wid)}" data-cn="${esc(w.nick)}"><div class="grow"><div class="title">${esc(w.title)}</div>
      <div class="small faint">${[w.mc, `${CLOUD_SIZE(w.size)}`, `с ПК «${w.device_name}»`, cloudDate(w.at)].filter(Boolean).map(esc).join(' · ')}</div>
      ${w.upload ? `<div class="small faint">Сейчас с ПК «${esc(w.upload.device_name)}» идёт отправка ${''}<button class="btn sm ghost" data-c="abort">Отменить загрузку</button></div>` : ''}</div>
      <button class="btn sm" data-c="download">${ic('download')} Скачать в сборку…</button>
      ${w.prev ? `<button class="btn sm ghost" data-c="prev" title="Вернуть прошлую облачную версию">${ic('refresh')}</button>` : ''}
      <button class="btn sm danger" data-c="del" title="Удалить из облака">${ic('trash')}</button></div>`).join('')}</div>`;
}

/** Обработчик кнопок облака на вкладке «Миры». Возвращает true, если событие обработано. */
async function cloudHandleClick(e, inst, body, rerender) {
  const c = e.target.closest('[data-c]')?.dataset.c; if (!c) return false;
  const w = e.target.closest('[data-w]')?.dataset.w;
  const cw = e.target.closest('[data-cw]');
  const run = async (p, ok) => { const r = await safe(p); if (r && r.error) toast(r.error, 'error'); else if (ok) toast(ok); rerender(); return r; };
  if (c === 'gosettings') go('settings');
  else if (c === 'choose') openCloudChoose(inst.id, w, rerender);
  else if (c === 'send') await run(api.cloud_send_now(inst.id, w), 'Отправляю мир...');
  else if (c === 'redownload') await run(api.cloud_redownload(inst.id, w), 'Скачиваю мир из облака...');
  else if (c === 'reupload') {
    if (await confirmDialog('Загрузить мир заново?', 'Мир с этого ПК уйдёт в облако как новый облачный мир.', 'Загрузить')) await run(api.cloud_reupload(inst.id, w), 'Отправляю мир...');
  } else if (c === 'download' && cw) {
    let r = await safe(api.cloud_download(inst.id, cw.dataset.cn, cw.dataset.cw, false));
    if (r && r.warn) {
      const text = r.warn.map((it) => (it.code === 'version' ? `Мир сохранён в Minecraft ${it.cloud}, а сборка — ${it.here}. В старой версии мир может сломаться.`
        : `В этой сборке${it.missing.length ? ' нет модов, которые были на другом ПК: ' + it.missing.join(', ') : ' другой загрузчик'}. Блоки и предметы этих модов могут пропасть из мира.`)).join('\n');
      if (!await confirmDialog('Скачать мир в эту сборку?', text, 'Всё равно')) return true;
      r = await safe(api.cloud_download(inst.id, cw.dataset.cn, cw.dataset.cw, true));
    }
    if (r && r.error) toast(r.error, 'error'); else toast('Скачиваю мир из облака...');
    rerender();
  } else if (c === 'del' && cw) {
    if (await confirmDialog('Удалить мир из облака?', 'Мир в облаке будет удалён. На компьютерах он останется.', 'Удалить', true)) await run(api.cloud_delete(cw.dataset.cn, cw.dataset.cw), 'Удалено из облака');
  } else if (c === 'prevlocal') {
    const d = e.target.closest('[data-c]').dataset;
    if (await confirmDialog('Вернуть прошлую версию мира?',
      `Прошлая облачная версия: ${cloudDate(+d.pat)}, с ПК «${d.pdev}», ${CLOUD_SIZE(+d.psz)}. Она станет текущей в облаке, а мир на этом ПК при следующем запуске обновится до неё (старая папка уйдёт в корзину сборки на 7 дней). Прошлые версии хранятся в облаке 3 дня.`,
      'Вернуть')) await run(api.cloud_restore_prev(d.pn, d.pw), 'Прошлая версия возвращена');
  } else if (c === 'prev' && cw) {
    if (await confirmDialog('Вернуть прошлую облачную версию?', 'Она станет новой текущей версией, остальные компьютеры получат её как обычное обновление.', 'Вернуть')) await run(api.cloud_restore_prev(cw.dataset.cn, cw.dataset.cw), 'Прошлая версия возвращена');
  } else if (c === 'abort' && cw) await run(api.cloud_abort_remote(cw.dataset.cn, cw.dataset.cw), 'Загрузка отменена');
  else if (c === 'toggle') {
    const on = e.target.closest('[data-c]').dataset.on === '1';
    if (on) {
      let r = await safe(api.cloud_enable_world(inst.id, w, ''));
      if (r && r.similar) {
        const s = r.similar[0];
        const m = openModal(`<h3>Это тот же мир?</h3><p class="muted">В облаке уже есть мир «${esc(r.title)}» (с ПК «${esc(s.device_name)}», ${cloudDate(s.at)}).</p>
          <div class="foot"><button class="btn ghost" data-x="new">Нет, отдельный мир</button><button class="btn primary" data-x="link">Да, выбрать копию</button></div>`);
        m.onclick = async (ev) => {
          const x = ev.target.closest('[data-x]')?.dataset.x; if (!x) return;
          m.close();
          const rr = await safe(api.cloud_enable_world(inst.id, w, x === 'link' ? 'link:' + s.wid : 'new'));
          if (rr && rr.error) toast(rr.error, 'error'); rerender();
        };
        return true;
      }
      if (r && r.error) toast(r.error, 'error');
    } else {
      await safe(api.cloud_disable_world(inst.id, w));
    }
    rerender();
  }
  return true;
}
