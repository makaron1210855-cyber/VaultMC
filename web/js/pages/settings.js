/* Настройки. Каждое изменение сразу сохраняется в config.json (через api.save_settings). */

const ACCENTS = {
  'Алмазный': ['#2fd3e6', '#67e8f9', '47, 211, 230', '#03181c'],
  'Золотой': ['#f2b63c', '#f7cd6e', '242, 182, 60', '#1a1203'],
  'Неон-зелёный': ['#22c55e', '#4ade80', '34, 197, 94', '#04130a'],
  'Фиолетовый': ['#7c4dff', '#9b7bff', '124, 77, 255', '#ffffff'],
  'Синий': ['#3b82f6', '#60a5fa', '59, 130, 246', '#ffffff'],
  'Оранжевый': ['#f97316', '#fb923c', '249, 115, 22', '#160700'],
  'Розовый': ['#ec4899', '#f472b6', '236, 72, 153', '#ffffff'],
  'Бирюзовый': ['#14b8a6', '#2dd4bf', '20, 184, 166', '#03110f'],
};
function applyAccent(name) {
  const a = ACCENTS[name] || ACCENTS['Алмазный'];
  const r = document.documentElement.style;
  r.setProperty('--accent', a[0]); r.setProperty('--accent-2', a[1]); r.setProperty('--accent-rgb', a[2]); r.setProperty('--on-accent', a[3]);
}
const getAccentHex = () => (ACCENTS[S.config.accent] || ACCENTS['Алмазный'])[0];

async function saveSetting(values) {
  const cfg = await safe(api.save_settings(values));
  if (cfg) Object.assign(S.config, cfg);
}

registerPage('settings', {
  render(el, params = {}) { this.refresh(el); if (params.focus === 'cf') setTimeout(() => $('[data-key=curseforge_api_key]', el)?.focus(), 350); },
  refresh(el) {
    const c = S.config, total = S.boot.ramTotal;
    el.innerHTML = `<div class="settings">
      <div class="h1">Настройки</div>

      <div class="card"><div class="row"><div class="grow"><div class="card-title">${ic('cpu')} Оперативная память</div>
        <p class="card-sub" id="ramHint"></p></div><div class="ram-value" id="ramVal">${c.ram_gb} ГБ</div></div>
        <input type="range" min="${S.boot.ramMin}" max="${S.boot.ramMax}" value="${c.ram_gb}" data-key="ram_gb">
        <div class="presets">${[2, 4, 6, 8, 12, 16].map((g) => `<button data-ram="${g}">${g} ГБ</button>`).join('')}</div></div>

      <div class="card"><div class="card-title">${ic('zap')} Производительность игры</div>
        <p class="card-sub">Общие настройки для всех сборок. Моды оптимизации и пресеты графики — на странице сборки.</p>
        <div class="set-grid">
          ${switchHtml('optimize_jvm', 'Флаги сборщика мусора G1', 'Меньше фризов и подтормаживаний', c.optimize_jvm)}
          ${switchHtml('high_priority', 'Высокий приоритет игры', 'Windows отдаёт игре больше процессора', c.high_priority)}
          ${switchHtml('discrete_gpu', 'Мощная видеокарта', 'Для ноутбуков с двумя видеокартами', c.discrete_gpu)}
        </div>
        <label class="field"><span>Дополнительные аргументы JVM (для всех сборок)</span><input class="input mono" data-key="extra_jvm_args" value="${esc(c.extra_jvm_args)}" placeholder="-Dfile.encoding=UTF-8"></label></div>

      <div class="card"><div class="card-title">${ic('gamepad')} Окно игры</div>
        <div class="row"><label class="field"><span>Ширина</span><input class="input" type="number" data-key="window_width" value="${c.window_width}" style="width:120px"></label>
          <label class="field"><span>Высота</span><input class="input" type="number" data-key="window_height" value="${c.window_height}" style="width:120px"></label>
          <div style="margin-left:16px">${switchHtml('fullscreen', 'Полный экран', '', c.fullscreen)}</div></div></div>

      <div class="card"><div class="card-title">${ic('cpu')} Java</div>
        ${switchHtml('java_auto', 'Автоматически подбирать Java под версию игры', 'Нужная версия Java берётся из описания версии', c.java_auto)}
        <div class="row" style="margin-top:8px"><input class="input mono grow" data-key="java_path" value="${esc(c.java_path)}" placeholder="Путь к java / javaw.exe (если автовыбор выключен)">
          <button class="btn" data-a="pickJava">Обзор</button><button class="btn" data-a="findJava">${ic('search')} Найти</button></div>
        <div id="javaList" class="list" style="margin-top:10px"></div></div>

      ${S.boot.builtinCfKey && !MS_LOGIN_ENABLED ? '' : `<div class="card"><div class="card-title">${ic('puzzle')} ${S.boot.builtinCfKey ? 'Вход через Microsoft' : 'Каталоги и вход'}</div>
        ${S.boot.builtinCfKey ? '' : `<label class="field"><span>API-ключ CurseForge (бесплатно на console.curseforge.com) — нужен только для CurseForge</span>
          <input class="input mono" data-key="curseforge_api_key" value="${esc(c.curseforge_api_key)}" placeholder="$2a$10$..."></label>`}
        ${MS_LOGIN_ENABLED ? `<label class="field"><span>Client ID приложения Microsoft — нужен только для входа с лицензией (см. README)</span>
          <input class="input mono" data-key="ms_client_id" value="${esc(c.ms_client_id)}" placeholder="00000000-0000-0000-0000-000000000000"></label>` : ''}</div>`}

      <div class="card"><div class="card-title">${ic('star')} Лаунчер</div>
        <div class="field"><span>Цвет интерфейса</span><div class="accents" id="accents">${Object.entries(ACCENTS).map(([n, a]) =>
          `<button title="${esc(n)}" style="background:${a[0]}" class="${n === c.accent ? 'on' : ''}" data-accent="${esc(n)}"></button>`).join('')}</div></div>
        <div class="field"><span>Язык интерфейса · Language</span><div class="seg" id="langSeg">
          <button class="${(c.ui || {}).lang === 'en' ? '' : 'on'}" data-lang="ru">Русский</button>
          <button class="${(c.ui || {}).lang === 'en' ? 'on' : ''}" data-lang="en">English</button></div></div>
        <div class="field"><span>Фон главного экрана</span><div class="row">
          <div class="bg-preview" id="bgPreview" style="${S.lobbyBg ? `background-image:url('${S.lobbyBg}')` : ''}">${S.lobbyBg ? '' : 'стандартный'}</div>
          <button class="btn" data-a="bgPick">${ic('image')} Выбрать фон...</button>
          <button class="btn ghost" data-a="bgClear" ${S.lobbyBg ? '' : 'disabled'}>Убрать фон</button></div></div>
        <label class="field"><span>Папка с общими файлами игры (версии, библиотеки, ассеты)</span>
          <div class="row"><input class="input mono grow" data-key="data_dir" value="${esc(c.data_dir)}"><button class="btn" data-a="pickDir">Обзор</button>
          <button class="btn" data-a="openData">${ic('folder')}</button></div></label>
        <label class="field"><span>Потоков загрузки: <b id="thrVal">${c.download_threads}</b></span><input type="range" min="4" max="64" value="${c.download_threads}" data-key="download_threads"></label>
        ${switchHtml('show_online', 'Показывать друзьям, что я в сети', 'Друзья VaultMC видят «в сети» / «играет в …» и могут зайти в вашу комнату', c.show_online)}
        ${switchHtml('auto_world_backups', 'Автокопии миров', 'После игры лаунчер копирует миры, в которых вы играли (5 последних копий на мир, миры больше 2 ГБ — только вручную)', c.auto_world_backups)}
        ${switchHtml('skins_in_game', 'Скины друзей в игре', 'Лаунчер ставит CustomSkinLoader в сборки с модами — все видят скины из мастерской', c.skins_in_game)}
        ${switchHtml('demo_mode', 'Режим имитации', 'Собрать команду запуска, но не запускать игру (для проверки)', c.demo_mode)}
        <div class="row" style="margin-top:10px"><button class="btn" data-a="shortcut">${ic('star')} Ярлык на рабочем столе</button>
          <button class="btn ghost" data-a="openLauncher">${ic('folder')} Папка лаунчера</button>
          <button class="btn ghost" data-a="tutorial">${ic('star')} Пройти обучение заново</button></div>
        <div class="row" style="margin-top:10px;align-items:center"><span class="muted grow">Версия VaultMC ${esc(S.boot.version)}</span>
          <button class="btn" data-a="checkUpdate">${ic('download')} Проверить обновления</button></div></div>

      ${S.account?.kind === 'offline' ? `<div class="card"><div class="card-title">${ic('key')} Пароль аккаунта ${esc(S.account.username)}</div>
        <div class="row"><input class="input" type="password" id="pw0" placeholder="Текущий пароль"><input class="input" type="password" id="pw1" placeholder="Новый пароль">
        <input class="input" type="password" id="pw2" placeholder="Повторите"><button class="btn primary" data-a="pw">Сменить</button></div>
        <p class="small muted" style="margin:8px 0">После смены пароля старый перестаёт действовать на всех компьютерах.</p>
        <div class="row wrap"><button class="btn" data-a="recovery">${ic('key')} Получить код восстановления</button>
          <button class="btn" data-a="rename">${ic('pencil')} Сменить ник</button></div></div>` : ''}
      <p class="note">Все настройки, аккаунты, сборки и друзья сохраняются автоматически и остаются после перезапуска и переустановки VaultMC.</p>
    </div>`;
    hydrateIcons(el);
    const ramHint = () => {
      const v = S.config.ram_gb;
      $('#ramHint', el).textContent = total ? `В системе ${total} ГБ. ` + (v > total - 2 ? 'Оставьте системе хотя бы 2 ГБ!' : 'Для обычной игры хватает 4 ГБ, для модов — 6–8 ГБ.') : 'Для обычной игры хватает 4 ГБ, для модов — 6–8 ГБ.';
      $('#ramHint', el).style.color = total && v > total - 2 ? 'var(--warn)' : '';
    };
    ramHint();

    // .oninput/.onchange, а не addEventListener: refresh() вызывается повторно на той же странице (например, после
    // смены аккаунта) — обработчики копились, и каждая настройка сохранялась по два-три раза
    el.oninput = (e) => {
      const k = e.target.dataset.key; if (!k) return;
      if (k === 'ram_gb') { S.config.ram_gb = +e.target.value; $('#ramVal', el).textContent = e.target.value + ' ГБ'; ramHint(); }
      if (k === 'download_threads') $('#thrVal', el).textContent = e.target.value;
    };
    el.onchange = (e) => {
      const k = e.target.dataset.key; if (!k) return;
      const v = e.target.type === 'checkbox' ? e.target.checked : e.target.type === 'range' || e.target.type === 'number' ? +e.target.value : e.target.value.trim();
      saveSetting({ [k]: v });
      if (e.target.type !== 'range') toast('Сохранено', 'info', 1400);
    };
    el.onclick = async (e) => {
      const ram = e.target.dataset.ram;
      if (ram) { $('[data-key=ram_gb]', el).value = ram; S.config.ram_gb = +ram; $('#ramVal', el).textContent = ram + ' ГБ'; ramHint(); saveSetting({ ram_gb: +ram }); }
      const acc = e.target.closest('[data-accent]')?.dataset.accent;
      if (acc) {
        applyAccent(acc); await saveSetting({ accent: acc });
        $$('#accents button', el).forEach((b) => b.classList.toggle('on', b.dataset.accent === acc));
        renderAccount(); renderPlaybar();
      }
      const lang = e.target.closest('[data-lang]')?.dataset.lang;
      if (lang) {
        (S.config.ui = S.config.ui || {}).lang = lang; await api.save_ui('lang', lang);
        if (lang === 'ru' && I18N.lang === 'en') return location.reload();   // обратно на русский — проще перерисовать всё
        setLanguage(lang); $$('#langSeg button', el).forEach((x) => x.classList.toggle('on', x.dataset.lang === lang));
      }
      const a = e.target.closest('[data-a]')?.dataset.a;
      if (a === 'bgPick') {
        const r = await safe(api.pick_lobby_bg());
        if (r && r.error) toast(r.error, 'error');
        if (r && r.url) { S.lobbyBg = r.url; toast('Фон обновлён — откройте главный экран'); }
      }
      if (a === 'bgClear') { await api.clear_lobby_bg(); S.lobbyBg = ''; }
      if (a === 'bgPick' || a === 'bgClear') {
        const pv = $('#bgPreview', el);
        pv.style.backgroundImage = S.lobbyBg ? `url('${S.lobbyBg}')` : ''; pv.textContent = S.lobbyBg ? '' : 'стандартный';
        $('[data-a=bgClear]', el).disabled = !S.lobbyBg;
      }
      if (a === 'pickJava') { const p = await safe(api.pick_java()); if (p) { $('[data-key=java_path]', el).value = p; saveSetting({ java_path: p }); } }
      if (a === 'findJava') {
        const box = $('#javaList', el); box.innerHTML = '<div class="row muted"><span class="spinner"></span> Ищу Java...</div>';
        const list = (await safe(api.find_java())) || [];
        box.innerHTML = list.length ? list.map((j) => `<div class="item" style="padding:8px 12px"><span class="chip accent">Java ${j.major}</span>
          <span class="grow small mono">${esc(j.path)}</span><button class="btn sm" data-use="${esc(j.path)}">Использовать</button></div>`).join('')
          : '<div class="empty"><b>Java не найдена</b><span>Установите Eclipse Temurin (adoptium.net): Java 21 для новых версий, Java 8 для старых.</span></div>';
      }
      const use = e.target.dataset.use;
      if (use) { $('[data-key=java_path]', el).value = use; saveSetting({ java_path: use }); toast('Java выбрана'); }
      if (a === 'pickDir') { const p = await safe(api.pick_folder()); if (p) { $('[data-key=data_dir]', el).value = p; saveSetting({ data_dir: p }); } }
      if (a === 'openData') api.open_folder('data');
      if (a === 'openLauncher') api.open_folder('launcher');
      if (a === 'tutorial') showTutorial();
      if (a === 'checkUpdate') checkUpdates(true);
      if (a === 'shortcut') { const r = await safe(api.create_shortcut()); if (r && okOrToast(r)) toast('Ярлык создан: ' + r.path); }
      if (a === 'recovery') {
        if (!await confirmDialog('Получить код восстановления?', 'Новый код заменит прежний: старый перестанет работать. Сохраните новый код сразу.', 'Получить код')) return;
        const r = await safe(api.recovery_code());
        if (r && okOrToast(r)) showRecoveryCode(r.code);
      }
      if (a === 'rename') openRenameDialog();
      if (a === 'pw') {
        const r = await safe(api.change_password($('#pw0', el).value, $('#pw1', el).value, $('#pw2', el).value));
        if (r && okOrToast(r)) { toast('Пароль изменён'); ['#pw0', '#pw1', '#pw2'].forEach((s) => ($(s, el).value = '')); }
      }
    };
  },
});
