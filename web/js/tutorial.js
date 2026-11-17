/* Обучение при первом входе: несколько шагов. Показывается один раз, повторить — в Настройках. */

const hasCfKey = () => !!(S.config.curseforge_api_key || (S.boot && S.boot.builtinCfKey));

function tutorialSteps() {
  return [
    {
      icon: 'zap', title: 'Добро пожаловать в VaultMC',
      html: `<p>Короткое знакомство на 1 минуту. Его можно пропустить и пройти позже в Настройках.</p>
        <ul class="tut-list">
          <li>${ic('check')} Играть можно без лицензии: оффлайн или через Ely.by</li>
          <li>${ic('check')} Java для игры лаунчер скачает сам</li>
          <li>${ic('check')} Моды ставятся одной кнопкой, вместе с зависимостями</li>
          <li>${ic('check')} Всё, что вы настроите, сохраняется само</li>
        </ul>`,
    },
    {
      icon: 'box', title: 'Шаг 1. Сборка — это ваша версия игры',
      html: `<p>Сборка — отдельная папка игры: своя версия Minecraft, свои моды, миры и настройки. Сборок можно сделать сколько угодно, и они не мешают друг другу.</p>
        <ul class="tut-list">
          <li>${ic('box')} <b>Vanilla</b> — обычная игра без модов</li>
          <li>${ic('puzzle')} <b>Fabric / Forge / NeoForge</b> — игра с модами (выбирайте их, если нужны моды)</li>
          <li>${ic('download')} Игра скачается сама при создании сборки</li>
        </ul>`,
      action: S.instances.length ? null : { label: 'Создать первую сборку', icon: 'plus', run: () => { finishTutorial(); openNewInstance(); } },
    },
    hasCfKey() ? {
      icon: 'puzzle', title: 'Шаг 2. Моды в один клик',
      html: `<p>Во вкладке <b>«Моды»</b> есть каталоги <b>Modrinth</b> и <b>CurseForge</b> — оба работают сразу, ничего настраивать не нужно.</p>
        <ul class="tut-list">
          <li>${ic('search')} Найдите мод и нажмите «Установить» — нужные зависимости встанут сами</li>
          <li>${ic('check')} Отметьте несколько модов галочками и скачайте пачкой</li>
          <li>${ic('archive')} Ресурспаки, шейдеры и готовые сборки — там же, в соседних вкладках</li>
        </ul>`,
    } : {
      icon: 'puzzle', title: 'Шаг 2. Моды: Modrinth и CurseForge',
      html: `<p><b>Modrinth</b> работает сразу, без настроек — там большинство модов.</p>
        <p><b>CurseForge</b> пускает в свой каталог по ключу. В больших лаунчерах (Prism и др.) ключ встроил разработчик, поэтому там вводить ничего не нужно.
        ${hasCfKey() ? '' : 'Здесь можно подключить свой ключ — он бесплатный. Или пропустите этот шаг: всегда можно вернуться в Настройках.'}</p>
        ${hasCfKey()
          ? `<div class="tut-ok">${ic('check')} CurseForge уже подключён</div>`
          : `<ol class="tut-steps">
              <li>Откройте <a href="#" data-url="https://console.curseforge.com/">console.curseforge.com</a> и войдите (можно через Google)</li>
              <li>Слева нажмите <b>API keys</b> и скопируйте ключ</li>
              <li>Вставьте его сюда и нажмите «Сохранить»</li>
            </ol>
            <div class="row"><input class="input mono grow" id="tutCf" placeholder="$2a$10$..."><button class="btn primary" id="tutCfSave">Сохранить</button></div>`}`,
      bind(card) {
        $$('[data-url]', card).forEach((a) => (a.onclick = (e) => { e.preventDefault(); api.open_url(a.dataset.url); }));
        const save = $('#tutCfSave', card);
        if (save) save.onclick = async () => {
          const v = $('#tutCf', card).value.trim();
          if (v.length < 20) return toast('Ключ слишком короткий — скопируйте его целиком', 'warn');
          await saveSetting({ curseforge_api_key: v });
          toast('Ключ CurseForge сохранён');
          showTutorialStep(card.__step);
        };
      },
    },
    {
      icon: 'globe', title: 'Шаг 3. Игра с друзьями и FPS',
      html: `<ul class="tut-list">
          <li>${ic('globe')} <b>Друзья → «Открыть мир для друзей»</b> — получите код комнаты и отправьте другу: он зайдёт через интернет, без Radmin и Hamachi</li>
          <li>${ic('users')} <b>Друзья по нику</b> — видно, кто в сети, а когда друг открыл мир, всплывает окошко «Зайти»</li>
          <li>${ic('zap')} <b>Оптимизация</b> — на странице сборки одна кнопка ставит моды для FPS</li>
          <li>${ic('shirt')} <b>Скин</b> — войдите через Ely.by, и ваш скин увидят другие игроки</li>
        </ul>`,
    },
    {
      icon: 'check', title: 'Всё готово!',
      html: `<p>Выберите сборку внизу и нажмите <b>«Играть»</b>. В следующий раз открывайте лаунчер ярлыком на рабочем столе.</p>
        <p class="muted small">Если что-то пойдёт не так — вкладка «Консоль» покажет, что случилось.</p>`,
      action: { label: 'Начать играть', icon: 'play', run: () => finishTutorial() },
    },
  ];
}

function showTutorial() {
  // показали один раз — больше сами не открываем (раньше обучение всплывало при каждой смене аккаунта, если его закрыть крестиком/Esc)
  (S.config.ui = S.config.ui || {}).tutorialDone = true;
  api.save_ui('tutorialDone', true);
  closeModal();
  const root = $('#modalRoot');
  root.innerHTML = '';
  root.append(html('<div class="backdrop"></div>'), html('<div class="tutorial" id="tutorial"></div>'));
  root.classList.add('open');
  showTutorialStep(0);
}

function showTutorialStep(i) {
  const steps = tutorialSteps();
  const step = steps[i];
  const card = $('#tutorial');
  if (!card || !step) return;
  card.__step = i;
  const last = i === steps.length - 1;
  card.innerHTML = `
    <div class="tut-art">${ic(step.icon)}<span class="tut-count">${i + 1} / ${steps.length}</span></div>
    <div class="tut-body" style="animation:tut-in .35s var(--ease) both">
      <h3>${esc(step.title)}</h3>
      <div class="tut-text">${step.html}</div>
      <div class="tut-foot">
        <div class="tut-dots">${steps.map((_, k) => `<span class="${k === i ? 'on' : ''}"></span>`).join('')}</div>
        <div class="grow"></div>
        ${last ? '' : '<button class="btn ghost" data-t="skip">Пропустить</button>'}
        ${i ? '<button class="btn ghost" data-t="back">Назад</button>' : ''}
        ${step.action ? `<button class="btn ${last ? 'primary' : ''}" data-t="action">${ic(step.action.icon)} ${esc(step.action.label)}</button>` : ''}
        ${last ? '' : `<button class="btn primary" data-t="next">Далее ${ic('chevronRight')}</button>`}
      </div>
    </div>`;
  hydrateIcons(card);
  if (step.bind) step.bind(card);
  card.onclick = (e) => {
    const t = e.target.closest('[data-t]')?.dataset.t;
    if (t === 'next') showTutorialStep(i + 1);
    if (t === 'back') showTutorialStep(i - 1);
    if (t === 'skip') finishTutorial();
    if (t === 'action') step.action.run();
  };
}

function finishTutorial() {
  (S.config.ui = S.config.ui || {}).tutorialDone = true;
  api.save_ui('tutorialDone', true);
  closeModal();
}

function maybeShowTutorial() {
  if (S.account && !(S.config.ui || {}).tutorialDone) setTimeout(showTutorial, 600);
}
