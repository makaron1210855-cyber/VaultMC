/* Код сборки и ссылка на неё: «Экспорт → Код для друга» и «Сборки → По коду» (ссылка — site/pack.html). */

/* ---------------------------------------------------------------- сборка по коду */
async function shareInstanceCode(inst) {
  const m = openModal(`<h3>Код сборки «${esc(inst.name)}»</h3>
    <div class="share-wait muted small">${ic('refresh')} Собираю список модов и загружаю на сервер...</div>`);
  const r = await safe(api.share_instance(inst.id));
  if (!m.isConnected) return;
  if (!r || r.error) { m.close(); if (r?.error) toast(r.error, 'error', 6000); return; }
  const manual = r.manual || [];
  m.innerHTML = `<button class="btn ghost sm icon x" data-close>${ic('x')}</button>
    <h3>Код сборки «${esc(inst.name)}»</h3>
    <p class="muted small">Друг открывает «Сборки → По коду» и вводит этот код. Лаунчер сам поставит ту же версию игры, загрузчик${r.mods ? `, моды (${r.mods} шт. с Modrinth)` : ""} и настройки. Код работает 30 дней.</p>
    <div class="share-code mono">${esc(r.code)}</div>
    <p class="muted small">Или отправьте ссылку: друг откроет её, нажмёт «Открыть в VaultMC» — и лаунчер сам предложит поставить сборку. Если VaultMC у него ещё нет, на той же странице есть кнопка «Скачать».</p>
    ${manual.length ? `<p class="note">${ic('alert')} Эти файлы не с Modrinth, в код они не входят — передайте их другу отдельно: <b>${manual.slice(0, 10).map(esc).join(', ')}${manual.length > 10 ? ` и ещё ${manual.length - 10}` : ''}</b></p>` : ''}
    <div class="foot"><button class="btn ghost" data-close>Закрыть</button>
      <button class="btn" id="scLink">${ic('link')} Скопировать ссылку</button>
      <button class="btn primary" id="scCopy">${ic('copy')} Скопировать код</button></div>`;
  hydrateIcons(m);
  m.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
  $('#scCopy', m).onclick = () => copyText(r.code);
  $('#scLink', m).onclick = () => copyText(`${PACK_LINK_BASE}#${r.code}`);
}

const PACK_LINK_BASE = 'https://vaultmc.vaultmc.workers.dev/pack.html';   // страница сайта site/pack.html

function openImportByCode(prefill = '') {
  const m = openModal(`<h3>Сборка по коду</h3>
    <p class="muted small">Вставьте код из 6 символов, который прислал друг. Появится новая сборка с теми же модами и настройками.</p>
    <label class="field"><span>Код</span><input class="input mono" id="bcCode" maxlength="8" placeholder="K7XQ2M" style="text-transform:uppercase;letter-spacing:3px;font-size:18px"></label>
    <div class="foot"><button class="btn ghost" data-close>Отмена</button><button class="btn primary" id="bcGo">${ic('download')} Установить</button></div>`);
  const input = $('#bcCode', m);
  if (typeof prefill === 'string' && prefill) input.value = prefill;   // пришли по ссылке с сайта
  input.focus();
  const go1 = async () => {
    const code = input.value.trim();
    if (!code) return toast('Введите код', 'warn');
    $('#bcGo', m).disabled = true;
    const r = await safe(api.import_by_code(code));
    if (!m.isConnected) return;
    $('#bcGo', m).disabled = false;
    if (!r) return;
    if (r.error) return toast(r.error, 'error', 6000);
    m.close();
    toast(`Устанавливаю «${r.name}» от ${r.by} — прогресс внизу`);
  };
  $('#bcGo', m).onclick = go1;
  input.onkeydown = (e) => { if (e.key === 'Enter') go1(); };
}
