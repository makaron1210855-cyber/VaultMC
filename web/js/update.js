/* =============================================================================
   Обновления лаунчера.
   - При запуске тихо проверяем сайт; если вышла новая версия — показываем окно.
   - Во время игры окно не показываем: ждём, пока игра закроется.
   - «Позже» скрывает окно до следующего запуска; проверить вручную можно
     в меню аккаунта или в Настройках → «Проверить обновления».
   ============================================================================= */
const UPD = { info: null, dismissed: false, open: false, timer: null };

const gameRunning = () => S.state === 'running' || S.state === 'preparing';
const modalBusy = () => !S.account || $('#modalRoot')?.classList.contains('open');

async function checkUpdates(manual = false) {
  const r = manual ? await safe(api.check_update()) : await api.check_update().catch(() => null);
  if (!r) return;
  if (r.error) { if (manual) toast(r.error, 'warn', 6000); return; }
  if (!r.available) { if (manual) toast(`У вас последняя версия VaultMC ${r.current}`); return; }
  UPD.info = r;
  if (manual) UPD.dismissed = false;
  maybeShowUpdate(manual);
}

function maybeShowUpdate(manual = false) {
  if (!UPD.info || UPD.dismissed || UPD.open) return;
  if (gameRunning()) {
    if (manual) toast('Обновление выйдет после закрытия игры', 'info', 5000);
    return;  // покажем, когда игра закроется (событие state)
  }
  if (modalBusy() && !manual) {  // пользователь занят другим окном — спросим чуть позже
    clearTimeout(UPD.timer);
    UPD.timer = setTimeout(() => maybeShowUpdate(), 5000);
    return;
  }
  showUpdateModal();
}

function showUpdateModal() {
  const i = UPD.info;
  UPD.open = true;
  const m = openModal(`
    <div class="upd ${i.major ? 'major' : ''}">
      ${i.major ? `<div class="upd-badge">${ic('star')} Масштабное обновление</div>` : ''}
      <div class="upd-head"><img src="img/mark.png" alt=""><div>
        <h3>${i.major ? `Вышел VaultMC ${esc(i.version)} — новое поколение лаунчера` : `Вышло обновление VaultMC ${esc(i.version)}`}</h3>
        <p class="muted">У вас версия ${esc(i.current)}.${i.major ? ' Много нового — список ниже.' : ''} Сборки, миры и аккаунты останутся на месте.</p></div></div>
      ${i.notes ? `<div class="upd-notes">${notesHtml(i.notes)}</div>` : ''}
      <div class="upd-progress" hidden><div class="progress"><div id="updBar" style="width:0%"></div></div>
        <small class="muted" id="updText">Скачиваю обновление...</small></div>
      <div class="foot"><button class="btn ghost" data-later>Позже</button>
        <button class="btn primary" data-go>${ic('download')} ${i.major ? `Перейти на ${esc(i.version)}` : 'Обновить'}</button></div>
    </div>`, { onClose: () => { UPD.open = false; UPD.dismissed = true; } });
  m.querySelector('[data-later]').onclick = () => m.close();
  m.querySelector('[data-go]').onclick = async () => {
    const r = await safe(api.install_update());
    if (!r) return;
    if (r.error) return toast(r.error, 'warn', 6000);
    if (r.manual) { toast('Открыл страницу загрузки — скачайте и запустите установщик'); return m.close(); }
    $('.upd-progress', m).hidden = false;
    $$('.foot button', m).forEach((b) => (b.disabled = true));
    m.querySelector('[data-close]').style.display = 'none';
  };
}

onEvent('update_progress', (pct) => {
  const bar = $('#updBar'); if (bar) bar.style.width = pct + '%';
  const t = $('#updText'); if (t) t.textContent = `Скачиваю обновление... ${pct}%`;
});
onEvent('update_installing', (v) => {
  const t = $('#updText'); if (t) t.textContent = `Сейчас откроется окно установки VaultMC ${v}. Когда полоса дойдёт до конца, лаунчер запустится сам.`;
  const bar = $('#updBar'); if (bar) bar.style.width = '100%';
  // окно браузерного режима закрываем — установщик откроет новую версию сам
  setTimeout(() => { try { window.close(); } catch (e) { /* не страшно */ } }, 5000);
});
onEvent('update_failed', (msg) => {
  toast(msg, 'error', 10000);
  const root = $('#modalRoot');
  if (!root) return;
  $$('.upd .foot button', root).forEach((b) => (b.disabled = false));
  const x = $('[data-close]', root); if (x) x.style.display = '';
  const t = $('#updText', root); if (t) t.textContent = msg;
  // автообновление не вышло — даём скачать установщик с сайта вручную
  const go = $('.upd [data-go]', root);
  if (go) {
    go.innerHTML = `${ic('link')} Скачать с сайта`; hydrateIcons(go);
    go.onclick = () => { api.open_update_site(); toast('Открыл сайт — скачайте и запустите установщик'); };
  }
});
// игра закрылась — если ждало обновление, показываем
onEvent('state', (st) => { if (st !== 'running') setTimeout(() => maybeShowUpdate(), 1500); });

// лаунчер только что обновился — показываем, что нового
function showUpdatedModal(u) {
  const m = openModal(`
    <div class="upd ${u.major ? 'major' : ''}">
      ${u.major ? `<div class="upd-badge">${ic('star')} Масштабное обновление</div>` : ''}
      <div class="upd-head"><img src="img/mark.png" alt=""><div>
        <h3>${u.major ? `Добро пожаловать в VaultMC ${esc(u.version)}` : `VaultMC обновлён до ${esc(u.version)}`}</h3>
        <p class="muted">Обновление установлено. Сборки, миры и аккаунты на месте.</p></div></div>
      ${u.notes ? `<div class="upd-notes"><b>Что нового</b><br>${notesHtml(u.notes)}</div>` : ''}
      <div class="foot"><button class="btn primary" data-ok>Отлично</button></div>
    </div>`, { onClose: () => api.ack_update() });
  m.querySelector('[data-ok]').onclick = () => m.close();
}

// тихая проверка после запуска (когда интерфейс уже загрузился)
(function autoCheck() {
  const wait = setInterval(() => {
    if (!S.boot) return;
    clearInterval(wait);
    if (S.boot.justUpdated) {
      const show = () => (S.account && !modalBusy() ? showUpdatedModal(S.boot.justUpdated) : setTimeout(show, 1500));
      setTimeout(show, 900);
      return;  // только что обновились — заново проверять не нужно
    }
    setTimeout(() => checkUpdates(false), 4000);
  }, 500);
})();

// окно выбора файла/папки открыто — подсказка поверх лаунчера, чтобы его не потерять
onEvent('dialog', (open) => {
  let el = document.getElementById('dialogHint');
  if (!open) { if (el) el.remove(); return; }
  if (el) return;
  el = html(`<div id="dialogHint" class="dialog-hint"><div>${ic('folder')}<b>Открыто окно выбора файла</b>
    <span>Выберите файл в окне Windows. Если его не видно — оно на панели задач.</span></div></div>`);
  document.body.append(el); hydrateIcons(el);
});

/** Текст изменений → HTML: строки-заголовки («Главное в 4.0:») жирные, между блоками отступ. */
function notesHtml(text) {
  return String(text).split('\n').map((line) => {
    if (!line.trim()) return '<div style="height:8px"></div>';
    if (!line.startsWith('•') && line.endsWith(':')) return `<b class="upd-h">${esc(line)}</b>`;
    return esc(line);
  }).join('<br>').replace(/<br><div/g, '<div').replace(/<\/div><br>/g, '</div>');
}
