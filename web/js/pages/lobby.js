/* Главный экран VaultMC: «витрина».
   Слева — текущая сборка крупно и большая кнопка «Играть», справа — ваш персонаж на подставке под лучом света,
   снизу — полка с недавними сборками и быстрые действия. */

const QUICK = [
  { act: 'skin', icon: 'shirt', title: 'Скины', text: 'Редактор, плащи, каталог' },
  { act: 'rooms', icon: 'users', title: 'Играть с другом', text: 'Через интернет, без Radmin' },
  { act: 'optimize', icon: 'zap', title: 'Больше FPS', text: 'Моды оптимизации в 1 клик' },
  { act: 'catalog', icon: 'puzzle', title: 'Моды', text: 'Modrinth и CurseForge' },
];

registerPage('lobby', {
  render(el) {
    el.innerHTML = `
      <div class="lobby2 ${S.lobbyBg ? 'has-bg' : ''}" style="position:relative;${S.lobbyBg ? `--lobby-bg:url('${S.lobbyBg}')` : ''}">
        <div class="lobby-bg-tools" style="position:absolute;top:10px;right:12px;z-index:5;display:flex;gap:6px">
          <button class="btn sm icon" data-act="bgMenu" title="Фон главного экрана">${ic('plus')}</button>
        </div>
        <section class="hero plate">
          <div class="hero-info" id="heroInfo"></div>
          <div class="showcase">
            <div class="spot"></div>
            <div class="skin-wrap" id="lobbySkin" title="Нажмите на персонажа — он что-нибудь достанет из хранилища"></div>
            <div class="pedestal"></div>
            <button class="nametag" data-act="skin" id="nameTag"></button>
          </div>
        </section>
        <section class="quick">${QUICK.map((q) => `<button class="quick-card plate" data-act="${q.act}">
            <span class="qi">${ic(q.icon)}</span><span><b>${q.title}</b><small>${q.text}</small></span></button>`).join('')}</section>
        <section class="shelf">
          <div class="shelf-head"><b>Мои сборки</b><span class="row" style="gap:14px"><button class="link-btn" data-act="gallery">${ic('image')} Скриншоты</button><button class="link-btn" data-act="stats">${ic('clock')} Время в игре</button><button class="link-btn" data-act="instances">Все сборки ${ic('chevronRight')}</button></span></div>
          <div class="shelf-row" id="shelfRow"></div>
        </section>
      </div>`;
    // персонаж следит за курсором
    let raf = 0;
    el.addEventListener('mousemove', (e) => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; rigLookAt($('#lobbySkin', el), e.clientX, e.clientY); });
    });
    el.addEventListener('mouseleave', () => rigLookAt($('#lobbySkin', el), null));
    el.addEventListener('click', (e) => {
      const act = e.target.closest('[data-act]')?.dataset.act;
      if (act === 'skin') openSkinModal();
      if (act === 'rooms') go('rooms');
      if (act === 'catalog') go('catalog');
      if (act === 'instances') go('instances');
      if (act === 'gallery') openGallery();
      if (act === 'stats') openPlayStats();
      if (act === 'optimize') S.selected ? go('instance', { id: S.selected.id, tab: 'optimize' }) : openNewInstance();
      if (act === 'switch') openInstancePicker();
      if (act === 'picture' && S.selected) instancePictureMenu(e.target.closest('[data-act]'), S.selected);
      if (act === 'rename' && S.selected) heroRename(el);
      if (act === 'bgMenu') {
        const anchor = e.target.closest('[data-act]');
        const dd = openDropdown(anchor, `<div class="dd-title">ФОН ГЛАВНОГО ЭКРАНА</div>
          <button class="dd-item" data-p="pick">${ic('image')}<b>Выбрать картинку...</b></button>
          ${S.lobbyBg ? `<button class="dd-item" data-p="clear">${ic('x')}<b>Убрать фон</b></button>` : ''}`, 'right');
        dd.onclick = async (ev) => {
          const p = ev.target.closest('[data-p]')?.dataset.p;
          closeDropdown();
          if (p === 'pick') {
            const r = await safe(api.pick_lobby_bg());
            if (r && r.error) return toast(r.error, 'error');
            if (r && r.url) { S.lobbyBg = r.url; toast('Фон обновлён'); refreshPage(); }
          }
          if (p === 'clear') { await safe(api.clear_lobby_bg()); S.lobbyBg = ''; toast('Фон убран'); refreshPage(); }
        };
      }
      if (act === 'new') openNewInstance();
      const settingsOf = e.target.closest('[data-inst-settings]')?.dataset.instSettings;
      if (settingsOf) { selectInstance(settingsOf); return go('instance', { id: settingsOf, tab: 'settings' }); }
      const pick = e.target.closest('[data-pick-inst]')?.dataset.pickInst;
      if (pick) { selectInstance(pick); refreshPage(); }
    });
    el.addEventListener('keydown', (e) => { if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('.shelf-card')) { e.preventDefault(); e.target.click(); } });
    this.refresh(el);
  },

  async refresh(el) {
    const inst = S.selected;
    // фон главного экрана вешается на обёртку .lobby2 — обновляем её здесь, иначе новая картинка видна только после перезагрузки
    const wrap = el.querySelector('.lobby2');
    if (wrap) {
      wrap.classList.toggle('has-bg', !!S.lobbyBg);
      wrap.style.setProperty('--lobby-bg', S.lobbyBg ? `url('${S.lobbyBg}')` : '');
    }
    const info = $('#heroInfo', el);
    if (inst) {
      el.querySelector('.hero').style.setProperty('--inst-color', inst.color);
      info.innerHTML = `
        <div class="kicker">${ic('box')} СБОРКА · <button class="link-btn" data-act="switch">сменить ${ic('chevronDown')}</button></div>
        <div class="hero-title"><span class="hero-pic" data-act="picture" role="button" tabindex="0"><canvas></canvas><i class="hero-pic-edit">${ic('image')}</i></span>
          <div class="hero-name"><h1 data-act="rename" role="button" tabindex="0">${esc(inst.name)}</h1><i class="hero-edit" data-act="rename">${ic('pencil')}</i></div></div>
        <div class="hero-meta">
          <span>${esc(inst.loaderLabel)}</span><span>${esc(inst.game_version)}</span>
          <span>${inst.modsCount} ${plural(inst.modsCount, 'мод', 'мода', 'модов')}</span><span title="Наиграно в этой сборке">${ic('clock')} ${esc(inst.playTime)}</span>
          <span title="Когда запускали в последний раз">${inst.last_played ? `играл ${fmtWhen(inst.last_played)}` : 'ещё не запускали'}</span>
        </div>
        <div class="big-play" id="bigPlay"></div>`;
      drawInstanceIcon($('.hero-title canvas', info), inst.name, inst.color, inst.icon);
    } else {
      info.innerHTML = `
        <div class="kicker">${ic('box')} НЕТ СБОРОК</div>
        <div class="hero-title"><h1>Создайте первую сборку</h1></div>
        <p class="muted">Выберите версию игры — чистый Minecraft или с модами (Fabric, Forge, NeoForge). Или импортируйте готовую.</p>
        <div class="row"><button class="btn primary lg" data-act="new">${ic('plus')} Новая сборка</button>
          <button class="btn lg" data-act="instances">${ic('download')} Импорт</button></div>`;
    }
    renderBigPlay($('#bigPlay', el));
    // полка недавних сборок
    const list = [...S.instances].sort((a, b) => (b.last_played || 0) - (a.last_played || 0)).slice(0, 8);
    const row = $('#shelfRow', el);
    row.innerHTML = list.map((i) => `<div class="shelf-card ${inst && i.id === inst.id ? 'on' : ''}" role="button" tabindex="0" data-pick-inst="${esc(i.id)}" style="--c:${i.color}">
        <canvas></canvas><b>${esc(i.name)}</b><small>${esc(i.loaderLabel)} · ${esc(i.game_version)}</small>
        <span class="shelf-gear" role="button" data-inst-settings="${esc(i.id)}" title="Настройки сборки">${ic('gear')}</span></div>`).join('')
      + `<button class="shelf-card add" data-act="new">${ic('plus')}<b>Новая сборка</b></button>`;
    $$('canvas', row).forEach((c, k) => drawInstanceIcon(c, list[k].name, list[k].color, list[k].icon));
    const tag = $('#nameTag', el);
    if (S.account) tag.innerHTML = `<b>${esc(S.account.username)}</b><small>${esc(S.account.kindLabel)} · скин</small>`;
    hydrateIcons(el);
    await drawSkinRig3d($('#lobbySkin', el), await skinFor(S.account?.id), getAccentHex());
  },
});

function plural(n, one, few, many) {
  const a = Math.abs(n) % 100, b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  return b === 1 ? one : many;
}

function renderBigPlay(box) {
  if (!box) return;
  const [value, text] = S.progress;
  if (S.state === 'running') {
    box.innerHTML = `<button class="btn danger play" data-x="kill">${ic('x')} Закрыть игру</button>
      <div class="sub"><button class="btn sm" data-x="logs">${ic('terminal')} Логи</button></div>`;
  } else if (S.state === 'installing' || S.state === 'preparing') {
    box.innerHTML = `<button class="btn play" disabled><span class="spinner"></span>${Math.round(value * 100)}%</button>
      <div class="progress"><div style="width:${value * 100}%"></div></div>
      <div class="sub"><span class="small muted grow" style="align-self:center">${esc(text)}</span><button class="btn sm" data-x="cancel">Отмена</button></div>`;
  } else {
    box.innerHTML = `<button class="btn primary play" data-x="play"><span class="shine"></span>${ic('play')} Играть</button>
      <div class="sub"><button class="btn sm" data-x="folder">${ic('folder')} Папка</button><button class="btn sm" data-x="settings">${ic('sliders')} Настройки сборки</button></div>`;
  }
  box.onclick = (e) => {
    const x = e.target.closest('[data-x]')?.dataset.x;
    if (x === 'play') play();
    if (x === 'kill') api.kill_game();
    if (x === 'cancel') api.cancel();
    if (x === 'logs') go('logs');
    if (x === 'folder' && S.selected) api.open_folder('game', S.selected.id);
    if (x === 'settings' && S.selected) go('instance', { id: S.selected.id });
  };
  hydrateIcons(box);
}

/* ---------------------------------------------------------------- окно «Мой скин» */
async function openSkinModal() {
  const acc = S.account;
  if (!acc) return;
  if (acc.kind === 'offline') return go('skins');      // у оффлайн-аккаунта — полноценная мастерская
  const kindText = {
    offline: 'У оффлайн-аккаунта скин виден только в лаунчере. Чтобы его видели другие игроки, войдите через Ely.by.',
    elyby: 'Скин берётся с Ely.by и виден всем, кто играет с Ely.by. Сменить скин можно на сайте ely.by.',
    microsoft: 'Скин лицензионного аккаунта меняется на minecraft.net.',
  }[acc.kind];
  const m = openModal(`<h3>Мой скин</h3><p class="muted small">${esc(kindText)}</p>
    <div class="row" style="gap:22px;align-items:center">
      <div style="padding:18px;border-radius:16px;background:radial-gradient(closest-side, rgba(var(--accent-rgb),.45), transparent)">
        <canvas id="skinPreview" style="height:240px;image-rendering:pixelated"></canvas></div>
      <div class="grow">
        ${acc.kind === 'offline'
          ? `<button class="btn primary" data-a="file">${ic('upload')} Загрузить .png</button>
             <p class="note">Подойдёт любой скин Minecraft 64×64 или 64×32.</p>`
          : `<button class="btn primary" data-a="site">${ic('link')} Открыть сайт</button>
             <button class="btn ghost" data-a="reload" style="margin-top:8px">${ic('refresh')} Обновить</button>`}
      </div>
    </div>`);
  const draw = async () => drawSkin($('#skinPreview', m), await skinFor(acc.id), getAccentHex());
  draw();
  m.onclick = async (e) => {
    const a = e.target.closest('[data-a]')?.dataset.a;
    if (a === 'file') {
      const url = await safe(api.choose_offline_skin());
      if (url) { S.skins[acc.id] = url; draw(); renderAccount(); refreshPage(); toast('Скин обновлён'); }
    }
    if (a === 'site') api.open_skin_site();
    if (a === 'reload') { delete S.skins[acc.id]; await draw(); renderAccount(); refreshPage(); }
  };
}

// Название сборки на главной: клик — правка прямо на месте. Enter или клик в сторону сохраняет, Esc отменяет.
function heroRename(el) {
  const inst = S.selected;
  const box = el.querySelector('.hero-name');
  if (!inst || !box || box.querySelector('input')) return;
  const h1 = box.querySelector('h1'), pen = box.querySelector('.hero-edit');
  const input = document.createElement('input');
  input.className = 'hero-rename'; input.maxLength = 60; input.value = inst.name; input.spellcheck = false;
  h1.hidden = true; pen.hidden = true;
  box.prepend(input);
  input.focus(); input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const name = input.value.trim();
    if (save && name && name !== inst.name) {
      await safe(api.update_instance(inst.id, { name }));
      await reloadInstances(); renderPlaybar(); renderTitleInstance(); refreshPage();
      toast('Сборка переименована');
    } else {
      input.remove(); h1.hidden = false; pen.hidden = false;
    }
  };
  input.onkeydown = (e) => { if (e.key === 'Enter') finish(true); else if (e.key === 'Escape') finish(false); };
  input.onblur = () => finish(true);
}
