/* Вход и аккаунты: Оффлайн (свой ник и пароль), Ely.by (Microsoft — выключен флагом MS_LOGIN_ENABLED). Меню аккаунтов в заголовке. */

function authFormHtml(prefillNick = '') {
  return `
    <div class="auth-card">
      <h2 style="margin:0;font-size:24px" id="authTitle">Вход в аккаунт</h2>
      <p class="muted small" style="margin:6px 0 0">Лицензия Minecraft не нужна: оффлайн и Ely.by работают без неё.</p>
      ${segHtml('kind', [['offline', 'Оффлайн'], ['elyby', 'Ely.by'], ...(MS_LOGIN_ENABLED ? [['microsoft', 'Microsoft']] : [])], 'offline', true)}

      <div data-pane="offline">
        <label class="field"><span>Никнейм</span><input class="input" id="offNick" placeholder="Steve" value="${esc(prefillNick)}" maxlength="16"></label>
        <label class="field"><span>Пароль</span><input class="input" id="offPw" type="password" placeholder="••••••"></label>
        <div data-reg class="hidden"><div class="strength"><div id="pwStrength"></div></div>
          <p class="small auth-new">Такого аккаунта на этом компьютере ещё нет. Повторите пароль — и он будет создан.</p>
          <label class="field"><span>Повторите пароль</span><input class="input" id="offPw2" type="password" placeholder="••••••"></label></div>
        <p class="small muted" style="line-height:1.5">Ник и пароль — это ваш аккаунт VaultMC. С ними можно войти на любом компьютере, а друзья находят вас по нику.</p>
      </div>

      <div data-pane="elyby" class="hidden">
        <button class="btn primary" style="width:100%;height:46px;font-size:15px" id="elyOauth">Войти через сайт Ely.by</button>
        <p class="small muted" style="line-height:1.5">Откроется сайт Ely.by. Пароль вводите там — лаунчер его не видит.</p>
        <details class="ely-pw-login"><summary class="small muted">Войти по нику и паролю</summary>
        <label class="field"><span>Ник или e-mail Ely.by</span><input class="input" id="elyLogin" placeholder="nickname"></label>
        <label class="field"><span>Пароль Ely.by</span><input class="input" id="elyPw" type="password" placeholder="••••••"></label>
        <label class="field hidden" id="ely2fa"><span>Код двухфакторной аутентификации</span><input class="input mono" id="elyTotp" placeholder="123456" maxlength="6"></label>
        <p class="small muted">Нет аккаунта? <a href="#" id="elyReg">Зарегистрироваться на Ely.by</a> — бесплатно, затем войдите здесь.</p>
        </details>
      </div>

      <div data-pane="microsoft" class="hidden">
        <p class="small muted" style="line-height:1.5">Вход с лицензией Minecraft Java. Откроется сайт Microsoft — введите там код, который появится ниже.</p>
        <div id="msBox"></div>
      </div>

      <div class="form-error" id="authError"></div>
      <button class="btn primary" style="width:100%;height:46px;font-size:15px" id="authGo">Войти</button>
      <p class="note">Данные хранятся только на этом компьютере. Пароли оффлайн-аккаунтов — в зашифрованном виде, пароль Ely.by не сохраняется (только токен сессии).</p>
    </div>`;
}

function bindAuthForm(root, onDone) {
  const st = { kind: 'offline', mode: 'login' };
  const err = (t) => ($('#authError', root).textContent = t || '');
  const update = () => {
    $$('[data-pane]', root).forEach((p) => p.classList.toggle('hidden', p.dataset.pane !== st.kind));
    $('[data-reg]', root).classList.toggle('hidden', st.mode !== 'register');
    const go = $('#authGo', root);
    const elyPwOpen = !!$('.ely-pw-login', root)?.open;
    go.classList.toggle('hidden', st.kind === 'microsoft' || (st.kind === 'elyby' && !elyPwOpen));
    go.textContent = st.kind === 'offline' && st.mode === 'register' ? 'Создать аккаунт' : 'Войти';
    $('#authTitle', root).textContent = st.kind === 'offline' && st.mode === 'register' ? 'Новый аккаунт' : 'Вход в аккаунт';
    if (st.kind === 'microsoft') renderMs();
    err('');
  };
  const renderMs = () => {
    const box = $('#msBox', root);
    if (!S.config.ms_client_id) {
      box.innerHTML = `<div class="key-card"><b>Microsoft требует «пропуск» для лаунчера</b>
        <p class="small muted" style="line-height:1.5;margin:8px 0 0">Каждый лаунчер, который входит через Microsoft, должен быть зарегистрирован
        у Microsoft, а Mojang должна одобрить доступ к Minecraft. У больших лаунчеров такой пропуск (Client ID) свой, и чужой брать нельзя.</p>
        <ol><li>Зарегистрируйте приложение на <a href="#" data-url="https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade">portal.azure.com</a> (бесплатно): тип «Personal Microsoft accounts only», в Authentication включите «Allow public client flows».</li>
        <li>Отправьте заявку Mojang на доступ к Minecraft API: <a href="#" data-url="https://aka.ms/mce-reviewappid">aka.ms/mce-reviewappid</a>. Проверка может занять несколько дней.</li>
        <li>Вставьте Application (client) ID сюда:</li></ol>
        <div class="row"><input class="input mono grow" id="msId" placeholder="00000000-0000-0000-0000-000000000000"><button class="btn primary" id="msSave">Сохранить</button></div>
        <p class="note">Пока пропуска нет — играйте через Оффлайн или Ely.by: всё работает без лицензии.</p></div>`;
      $$('[data-url]', box).forEach((a) => (a.onclick = (e) => { e.preventDefault(); api.open_url(a.dataset.url); }));
      $('#msSave', box).onclick = async () => {
        const v = $('#msId', box).value.trim();
        if (!/^[0-9a-f-]{36}$/i.test(v)) return toast('Client ID выглядит как 00000000-0000-0000-0000-000000000000', 'warn');
        await saveSetting({ ms_client_id: v }); toast('Client ID сохранён'); renderMs();
      };
      return;
    }
    box.innerHTML = `<button class="btn primary" style="width:100%;height:46px" id="msStart">${ic('shield')} Войти через Microsoft</button>`;
    hydrateIcons(box);
    $('#msStart', box).onclick = async () => {
      const r = await safe(api.ms_start());
      if (!r || !okOrToast(r)) return;
      box.innerHTML = `<p class="small muted">Откройте <a href="#" id="msUrl">${esc(r.url)}</a> и введите код:</p>
        <div class="code-box">${esc(r.code)}</div><div class="row" style="margin-top:10px"><span class="spinner"></span><span class="small muted grow">Жду подтверждения...</span>
        <button class="btn sm ghost" id="msCopy">Копировать код</button><button class="btn sm ghost" id="msCancel">Отмена</button></div>`;
      $('#msUrl', box).onclick = (e) => { e.preventDefault(); api.open_url(r.url); };
      $('#msCopy', box).onclick = () => copyText(r.code);
      $('#msCancel', box).onclick = () => { api.ms_cancel(); renderMs(); };
    };
  };
  root.__msDone = (res) => { if (res.error) { err(res.error); renderMs(); } else onDone(res); };
  bindSegs(root, (name, v) => { st[name] = v; update(); });
  $('#offPw', root).addEventListener('input', async (e) => {
    if (st.mode !== 'register') return;
    const score = await api.password_strength(e.target.value);
    const bar = $('#pwStrength', root);
    bar.style.width = (score ? score * 25 : 6) + '%';
    bar.style.background = ['#f04b4b', '#f04b4b', '#f5a524', '#84cc16', 'var(--accent)'][score];
  });
  // сменили ник — это уже может быть существующий аккаунт: снова обычный вход
  $('#offNick', root).addEventListener('input', () => { if (st.mode === 'register') { st.mode = 'login'; update(); } });
  $('#elyReg', root).onclick = (e) => { e.preventDefault(); api.elyby_register(); };
  $('.ely-pw-login', root).addEventListener('toggle', update);
  const elyBtn = $('#elyOauth', root);
  const elyBtnText = elyBtn.textContent;
  // результат входа на сайте Ely.by приходит событием elyby_oauth (см. onEvent ниже)
  root.__elyDone = (res) => {
    elyBtn.disabled = false; elyBtn.textContent = elyBtnText;
    if (res.error) err(res.error); else onDone(res);
  };
  elyBtn.onclick = async () => {
    err('');
    elyBtn.disabled = true; elyBtn.textContent = 'Ждём вход в браузере…';
    const r = await safe(api.elyby_oauth_start());
    if (!r || r.error) { elyBtn.disabled = false; elyBtn.textContent = elyBtnText; err(r?.error || 'Не вышло открыть Ely.by'); }
  };
  const submit = async () => {
    err('');
    const go = $('#authGo', root);
    go.disabled = true; go.innerHTML = '<span class="spinner"></span>';
    let res;
    if (st.kind === 'offline') {
      const nick = $('#offNick', root).value.trim(), pw = $('#offPw', root).value;
      res = await safe(api.enter_offline(nick, pw, st.mode === 'register' ? $('#offPw2', root).value : ''));
      if (res?.needRepeat) {   // аккаунта с таким ником здесь нет — показываем «повторите пароль» и создаём
        st.mode = 'register'; go.disabled = false; update();
        $('#offPw2', root).value = ''; $('#offPw2', root).focus();
        return;
      }
    } else if (st.kind === 'elyby') {
      res = await safe(api.login_elyby($('#elyLogin', root).value.trim(), $('#elyPw', root).value, $('#elyTotp', root).value.trim()));
      if (res?.need2fa) { $('#ely2fa', root).classList.remove('hidden'); $('#elyTotp', root).focus(); }
    }
    go.disabled = false; update();
    if (!res) return;
    if (res.error) return err(res.error);
    onDone(res);
  };
  $('#authGo', root).onclick = submit;
  root.addEventListener('keydown', (e) => { if (e.key === 'Enter' && e.target.matches('input')) submit(); });
  update();
  setTimeout(() => $('#offNick', root)?.focus(), 200);
}

/** Полноэкранный вход (первый запуск или нет активного аккаунта). */
function showAuth(prefillNick = '') {
  hideAuth();
  const wrap = html(`<div class="auth" id="authScreen">
    <div class="auth-hero"><div class="inner">
      <img src="img/logo.png" alt="">
      <h1>Играй в Minecraft<br>по-новому</h1>
      <p>Все версии, сборки с модами, Ely.by-скины и игра с друзьями — без лицензии.</p>
      <ul><li>${ic('check')} Релизы, снапшоты, бета и альфа</li><li>${ic('check')} Моды из Modrinth и CurseForge в 1 клик</li>
      <li>${ic('check')} Комнаты для игры с друзьями без Radmin</li><li>${ic('check')} Оптимизация: больше FPS одной кнопкой</li></ul>
    </div></div>
    <div class="auth-form">${authFormHtml(prefillNick)}</div></div>`);
  document.body.appendChild(wrap);
  hydrateIcons(wrap);
  window.__authRoot = wrap;
  bindAuthForm(wrap, (res) => { applyAccountResult(res); hideAuth(); toast(`Добро пожаловать, ${res.account.username}!`); });
}
function hideAuth() { $('#authScreen')?.remove(); }

/** То же в окне — «Добавить аккаунт». */
function openAddAccount(prefillNick = '') {
  closeDropdown();
  const m = openModal(authFormHtml(prefillNick));
  window.__authRoot = m;
  bindAuthForm(m, (res) => { applyAccountResult(res); m.close(); toast(`Аккаунт ${res.account.username} активен`); });
}

function applyAccountResult(res) {
  S.account = res.account; S.accounts = res.accounts || S.accounts;
  renderAccount();
  if (S.page === 'lobby' || S.page === 'settings') refreshPage();
  else go('lobby');
  maybeShowTutorial();  // первый вход — показываем обучение
}

/* ---------------------------------------------------------------- аватар и меню аккаунтов */
async function renderAccount() {
  const chip = $('#accountChip'), card = $('#profileCard');
  const acc = S.account;
  if (!acc) { chip.innerHTML = ''; card.innerHTML = ''; return; }
  chip.innerHTML = `<canvas></canvas><div class="who"><b>${esc(acc.username)}</b><small>${esc(acc.kindLabel)}</small></div>${ic('chevronDown')}`;
  card.innerHTML = `<canvas></canvas><div class="pc-text"><b>${esc(acc.username)}</b><span class="chip ${KIND_CHIP[acc.kind]}">${esc(acc.kindLabel)}</span></div>`;
  hydrateIcons(chip);
  const skin = await skinFor(acc.id);
  drawHead($('canvas', chip), skin, getAccentHex());
  drawHead($('canvas', card), skin, getAccentHex());
}

function openAccountMenu(anchor) {
  const locked = S.state === 'running' || S.state === 'preparing';   // игра уже вошла под этим аккаунтом
  const dd = openDropdown(anchor, `<div class="dd-title">АККАУНТЫ</div>
    ${locked ? `<div class="dd-note">${ic('lock')} Во время игры аккаунт сменить нельзя — сначала закройте игру</div>` : ''}
    ${S.accounts.map((a) => `<div class="dd-item ${a.id === S.account?.id ? 'active' : ''} ${locked && a.id !== S.account?.id ? 'locked' : ''}" data-id="${a.id}" role="button">
      <canvas></canvas><div class="grow"><b>${esc(a.username)}</b><small title="Наиграно с этим аккаунтом во всех сборках">${esc(a.kindLabel)} · ${esc(a.playTime)}</small></div>
      ${a.id === S.account?.id ? `<span class="ok">${ic('check')}</span>` : ''}
      ${locked && a.id === S.account?.id ? '' : `<button class="btn sm ghost icon" data-rm title="Удалить из лаунчера">${ic('trash')}</button>`}</div>`).join('')}
    <div class="dd-sep"></div>
    <button class="dd-item" data-add ${locked ? 'disabled' : ''}>${ic('plus')}<b>Добавить аккаунт</b></button>
    <button class="dd-item" data-upd>${ic('download')}<b>Проверить обновления</b></button>
    <button class="dd-item" data-out ${locked ? 'disabled' : ''}>${ic('logout')}<b>Выйти</b></button>`);
  $$('.dd-item[data-id]', dd).forEach(async (row) => drawHead($('canvas', row), await skinFor(row.dataset.id), getAccentHex()));
  dd.onclick = async (e) => {
    const row = e.target.closest('[data-id]');
    if (row && e.target.closest('[data-rm]')) {
      const a = S.accounts.find((x) => x.id === row.dataset.id);
      if (!(await confirmDialog('Удалить аккаунт?', `${a.username} (${a.kindLabel}) будет удалён из лаунчера. Сборки и миры останутся.`, 'Удалить', true))) return;
      const r = await api.remove_account(a.id);
      S.accounts = r.accounts; S.account = r.account; closeDropdown(); renderAccount();
      if (!S.account) showAuth();
      return;
    }
    if (row) {
      if (row.dataset.id === S.account?.id) return closeDropdown();
      if (locked) return toast('Во время игры аккаунт сменить нельзя — сначала закройте игру', 'warn');
      const r = await safe(api.switch_account(row.dataset.id));
      closeDropdown();
      if (!r) return;
      if (r.needPassword) return openAddAccount(r.username);
      if (okOrToast(r)) { applyAccountResult(r); toast(`Аккаунт: ${r.account.username}`); }
    }
    if (locked && e.target.closest('[data-add],[data-out]')) return toast('Во время игры аккаунт сменить нельзя — сначала закройте игру', 'warn');
    if (e.target.closest('[data-add]')) openAddAccount();
    if (e.target.closest('[data-upd]')) { closeDropdown(); checkUpdates(true); }
    if (e.target.closest('[data-out]')) { closeDropdown(); await api.logout(); S.account = null; renderAccount(); showAuth(); }
  };
}

onEvent('ms_login', (res) => { if (window.__authRoot?.__msDone) window.__authRoot.__msDone(res); });
onEvent('elyby_oauth', (res) => { if (window.__authRoot?.__elyDone) window.__authRoot.__elyDone(res); });
