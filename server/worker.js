/*
 * Сервер скинов VaultMC (Cloudflare Workers + KV, бесплатный тариф).
 *
 * Что делает:
 *   GET  /csl/<ник>.json                     — профиль для CustomSkinLoader (формат CustomSkinAPI): хэши текстур
 *   GET  /csl/textures/<хэш>, /textures/<хэш> — сами картинки. Хэш меняется вместе с картинкой, поэтому
 *                                              игра не показывает старый скин из своего кэша
 *   GET  /skins/<ник>.png, /capes/<ник>.png  — то же по нику (старые версии лаунчера, тип Legacy)
 *   POST /api/register   {name, token}                      — занять ник (token придумывает лаунчер)
 *   POST /api/upload     {name, token, kind, png, slim}     — kind: "skin" | "cape"; png — base64
 *   POST /api/remove     {name, token, kind}                — убрать плащ/скин
 *   POST /api/friends    {name, token, action, other}       — action: list | request | accept | decline | remove
 *                                                             (list отдаёт и статусы друзей: в сети / в игре / комната)
 *   POST /api/presence   {name, token, status}              — мой статус (null — «не в сети»)
 *   POST /api/share      {name, token, pack}                — сохранить сборку, ответ: {code}
 *   GET  /api/share/<код>                                    — сборка по коду
 *   POST /api/p2p        {name, token, action, host, id, sdp, code} — offer | inbox | answer | poll: прямое соединение (WebRTC)
 *   POST /api/room       {name, token, action, code, info}  — open | update | close: комната по коду (дружить не нужно)
 *   GET  /api/room/<код>                                     — к кому и в какую версию заходить по коду
 *   GET  /api/status                                         — проверка, что сервер работает
 *
 * Ники (версия 5): свободный ник занимает первый, кто пришёл. У ника до 5 ключей — один игрок на нескольких ПК
 * (ключ оффлайн-аккаунта лаунчер получает из ника и пароля, поэтому он одинаковый на всех компьютерах).
 * Занятый ник может забрать только владелец аккаунта Ely.by с этим ником (лаунчер делает «вход на сервер»
 * через Ely.by, а мы проверяем его через session/hasJoined). Запрета «ник есть на Ely.by» больше нет.
 * Бесплатный KV — около 1000 записей в сутки, поэтому статусы пишем только при изменениях.
 *
 * Хранилище: KV-пространство, привязанное к воркеру под именем SKINS.
 * Ник защищён токеном: кто первым занял ник с лаунчера, только тот и меняет его скин.
 */

const NAME_RE = /^[A-Za-z0-9_]{3,16}$/;
const MAX_PNG = 256 * 1024;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    let path;
    try { path = decodeURIComponent(url.pathname); } catch { return new Response('Not found', { status: 404 }); }
    try {
      if (request.method === 'GET') {
        const m = path.match(/^\/(skins|capes)\/([A-Za-z0-9_]{1,16})\.png$/);
        if (m) return texture(env, m[1] === 'skins' ? 'skin' : 'cape', m[2]);
        const cp = path.match(/^\/csl\/([A-Za-z0-9_]{1,16})\.json$/);
        if (cp) return cslProfile(env, cp[1]);
        const ct = path.match(/^\/(?:csl\/)?textures\/([0-9a-f]{40})([sc])([a-z0-9_]{1,16})$/);
        if (ct) return cslTexture(env, ct[1], ct[2] === 's' ? 'skin' : 'cape', ct[3]);
        if (path === '/api/status' || path === '/') return json({ ok: true, service: 'VaultMC skins', version: 5 });
        const sh = path.match(/^\/api\/share\/([A-Z0-9]{6})$/);
        if (sh) return getShare(env, sh[1]);
        const rm = path.match(/^\/api\/room\/([A-Za-z0-9]{6})$/);
        if (rm) return getRoom(env, rm[1].toUpperCase());
        return new Response('Not found', { status: 404 });
      }
      if (request.method !== 'POST') return json({ error: 'Метод не поддерживается' }, 405);
      const body = await request.json().catch(() => null);
      if (!body || typeof body !== 'object') return json({ error: 'Пустой запрос' }, 400);
      if (path === '/api/register') return register(env, body);
      const who = await auth(env, body);
      if (who.error) return json(who, 403);
      if (path === '/api/upload') return upload(env, who.name, body);
      if (path === '/api/remove') return remove(env, who.name, body);
      if (path === '/api/friends') return friends(env, who.name, body);
      if (path === '/api/presence') return presence(env, who.name, body);
      if (path === '/api/share') return putShare(env, who.name, body);
      if (path === '/api/p2p') return p2p(env, who.name, body);
      if (path === '/api/room') return room(env, who.name, body);
      if (path === '/api/turn') return turn(env);
      return json({ error: 'Неизвестный адрес' }, 404);
    } catch (err) {
      return json({ error: 'Ошибка сервера: ' + (err && err.message || err) }, 500);
    }
  },
};

/* ---------------------------------------------------------------- помощники */
const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
});
const key = (name) => name.toLowerCase();

async function sha256(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function getUser(env, name) {
  return env.SKINS.get('user:' + key(name), 'json');
}

async function auth(env, body) {
  const name = String(body.name || '');
  const token = String(body.token || '');
  if (!NAME_RE.test(name) || token.length < 32) return { error: 'Неверный ник или ключ' };
  const user = await getUser(env, name);
  if (!user) return { error: 'Ник не зарегистрирован на сервере VaultMC', code: 'auth' };
  if (!userHashes(user).includes(await sha256(token))) return { error: TAKEN(name), code: 'auth' };
  return { name: user.name };
}

// У ника может быть несколько ключей (до 5): один игрок на нескольких компьютерах.
// Старые записи хранят один ключ в поле hash.
const userHashes = (user) => (Array.isArray(user.hashes) && user.hashes.length ? user.hashes : [user.hash]);
const TAKEN = (name) => `Ник ${name} уже занят в VaultMC другим игроком. Если это ваш ник — войдите с тем же паролем, что и на другом компьютере; иначе выберите другой ник`;

function pngSize(bytes) {
  const sig = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
  if (bytes.length < 24 || sig.some((b, i) => bytes[i] !== b)) return null;
  const dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return { w: dv.getUint32(16), h: dv.getUint32(20) };
}

function fromBase64(b64) {
  const clean = String(b64 || '').replace(/^data:image\/png;base64,/, '');
  const bin = atob(clean);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function toBase64(bytes) {
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/* ---------------------------------------------------------------- ручки */
async function texture(env, kind, name) {
  const data = await env.SKINS.get(kind + ':' + key(name), 'json');
  if (!data) return new Response('Not found', { status: 404 });
  return new Response(fromBase64(data.png), {
    headers: { 'content-type': 'image/png', 'cache-control': 'no-cache', 'x-skin-model': data.slim ? 'slim' : 'default' },
  });
}

/* CustomSkinAPI: хэш = 40 знаков SHA-256 картинки + тип (s/c) + ник. Новая картинка — новый адрес,
   поэтому ни CustomSkinLoader, ни сама игра не покажут старый скин из своего кэша. */
async function pngHash(b64) {
  const buf = await crypto.subtle.digest('SHA-256', fromBase64(b64));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 40);
}

async function cslProfile(env, name) {
  const [skin, cape] = await Promise.all([env.SKINS.get('skin:' + key(name), 'json'), env.SKINS.get('cape:' + key(name), 'json')]);
  if (!skin && !cape) return new Response('Not found', { status: 404 });
  const out = { username: name };
  if (skin) out.skins = { [skin.slim ? 'slim' : 'default']: (skin.hash || await pngHash(skin.png)) + 's' + key(name) };
  if (cape) out.cape = (cape.hash || await pngHash(cape.png)) + 'c' + key(name);
  return new Response(JSON.stringify(out), { headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' } });
}

async function cslTexture(env, hash, kind, name) {
  const data = await env.SKINS.get(kind + ':' + key(name), 'json');
  if (!data) return new Response('Not found', { status: 404 });
  const cur = data.hash || await pngHash(data.png);
  // старый адрес: отдаём текущую картинку, но без долгого кэша
  const cache = cur === hash ? 'public, max-age=31536000, immutable' : 'no-store';
  return new Response(fromBase64(data.png), { headers: { 'content-type': 'image/png', 'cache-control': cache } });
}

/* Доказательство владения ником Ely.by: лаунчер сделал session/join с этим serverId */
async function elybyProven(name, serverId) {
  if (!serverId || !/^[A-Za-z0-9_-]{8,64}$/.test(String(serverId))) return false;
  try {
    const r = await fetch(`https://authserver.ely.by/session/hasJoined?username=${encodeURIComponent(name)}&serverId=${encodeURIComponent(serverId)}`);
    if (r.status !== 200) return false;
    const p = await r.json().catch(() => null);
    return !!p && String(p.name || '').toLowerCase() === name.toLowerCase();
  } catch { return false; }
}

async function register(env, body) {
  const name = String(body.name || '');
  const token = String(body.token || '');
  if (!NAME_RE.test(name)) return json({ error: 'Ник: 3–16 символов, латиница, цифры и _' }, 400);
  if (token.length < 32) return json({ error: 'Слишком короткий ключ' }, 400);
  const existing = await getUser(env, name);
  const hash = await sha256(token);
  const hashes = existing ? userHashes(existing) : [];
  if (hashes.includes(hash)) return json({ ok: true, already: true, ely: !!existing.ely });
  // Свободный ник занимает первый, кто пришёл — даже если такой ник есть на Ely.by (раньше оффлайн-игрока
  // с таким ником сервер не пускал совсем, и у него не работали друзья и комнаты).
  let keep = [];
  if (existing) {
    // Ник занят. Добавить свой ключ можно двумя способами:
    //  old — у лаунчера есть прежний ключ этого ника (обновление ключа на «ключ из пароля», тот же игрок);
    //  ely — игрок доказал, что это его аккаунт Ely.by. Если ник до этого держал оффлайн-игрок — он его теряет.
    const oldOk = body.old && String(body.old).length >= 32 && hashes.includes(await sha256(String(body.old)));
    const proven = !oldOk && body.ely ? await elybyProven(name, body.ely) : false;
    if (!oldOk && !proven) return json({ error: TAKEN(existing.name), code: 'taken' }, 409);
    keep = oldOk || existing.ely ? hashes : [];
    const ely = proven || !!existing.ely;
    await env.SKINS.put('user:' + key(name), JSON.stringify({ name: existing.name, hash, hashes: [...keep, hash].slice(-5), ely, created: existing.created }));
    return json({ ok: true, ely });
  }
  const proven = body.ely ? await elybyProven(name, body.ely) : false;
  await env.SKINS.put('user:' + key(name), JSON.stringify({ name, hash, hashes: [hash], ely: proven, created: Date.now() }));
  return json({ ok: true, ely: proven });
}

async function upload(env, name, body) {
  const kind = body.kind === 'cape' ? 'cape' : 'skin';
  let bytes;
  try { bytes = fromBase64(body.png); } catch { return json({ error: 'Картинка повреждена' }, 400); }
  if (bytes.length > MAX_PNG) return json({ error: 'Картинка слишком большая' }, 400);
  const size = pngSize(bytes);
  if (!size) return json({ error: 'Это не PNG' }, 400);
  const okSkin = size.w >= 64 && size.w <= 512 && size.w % 64 === 0 && (size.h === size.w || size.h === size.w / 2);
  const okCape = size.w >= 64 && size.w <= 512 && size.w % 64 === 0 && size.h === size.w / 2;
  if (kind === 'skin' ? !okSkin : !okCape) return json({ error: `Размер ${size.w}×${size.h} не подходит для ${kind === 'skin' ? 'скина' : 'плаща'}` }, 400);
  const png = toBase64(bytes);
  await env.SKINS.put(kind + ':' + key(name), JSON.stringify({ png, slim: !!body.slim, hash: await pngHash(png), at: Date.now() }));
  return json({ ok: true });
}

async function remove(env, name, body) {
  const kind = body.kind === 'cape' ? 'cape' : 'skin';
  await env.SKINS.delete(kind + ':' + key(name));
  return json({ ok: true });
}

/* ---------------------------------------------------------------- друзья (взаимные) */
async function getFriends(env, name) {
  return (await env.SKINS.get('friends:' + key(name), 'json')) || { friends: [], incoming: [], outgoing: [] };
}
const putFriends = (env, name, data) => env.SKINS.put('friends:' + key(name), JSON.stringify(data));
const without = (list, name) => list.filter((x) => key(x) !== key(name));
const has = (list, name) => list.some((x) => key(x) === key(name));

async function friends(env, name, body) {
  const action = String(body.action || 'list');
  const me = await getFriends(env, name);
  if (action === 'list') {
    const status = {};
    await Promise.all(me.friends.slice(0, 100).map(async (f) => {
      const st = await env.SKINS.get('presence:' + key(f), 'json');
      if (st) status[f] = st;
    }));
    return json({ ok: true, ...me, status });
  }

  const otherName = String(body.other || '');
  if (!NAME_RE.test(otherName)) return json({ error: 'Неверный ник друга' }, 400);
  if (key(otherName) === key(name)) return json({ error: 'Нельзя добавить самого себя' }, 400);
  const otherUser = await getUser(env, otherName);
  if (!otherUser) return json({ error: `Игрок ${otherName} ещё не заходил в VaultMC с этим ником` }, 404);
  const other = await getFriends(env, otherUser.name);

  if (action === 'request') {
    if (has(me.friends, otherUser.name)) return json({ ok: true, ...me });
    if (has(me.incoming, otherUser.name)) {          // он уже звал нас — сразу дружим
      me.incoming = without(me.incoming, otherUser.name);
      if (!has(me.friends, otherUser.name)) me.friends.push(otherUser.name);
      other.outgoing = without(other.outgoing, name);
      if (!has(other.friends, name)) other.friends.push(name);
    } else {
      if (me.outgoing.length > 200) return json({ error: 'Слишком много заявок' }, 429);
      // защита от спама: у одного игрока не больше 200 входящих заявок
      if (other.incoming.length > 200 && !has(other.incoming, name)) return json({ error: `У ${otherUser.name} слишком много заявок` }, 429);
      if (!has(me.outgoing, otherUser.name)) me.outgoing.push(otherUser.name);
      if (!has(other.incoming, name)) other.incoming.push(name);
    }
  } else if (action === 'accept') {
    if (!has(me.incoming, otherUser.name)) return json({ error: 'Заявки нет' }, 400);
    me.incoming = without(me.incoming, otherUser.name);
    if (!has(me.friends, otherUser.name)) me.friends.push(otherUser.name);
    other.outgoing = without(other.outgoing, name);
    if (!has(other.friends, name)) other.friends.push(name);
  } else if (action === 'decline' || action === 'remove') {
    me.incoming = without(me.incoming, otherUser.name);
    me.outgoing = without(me.outgoing, otherUser.name);
    me.friends = without(me.friends, otherUser.name);
    other.incoming = without(other.incoming, name);
    other.outgoing = without(other.outgoing, name);
    other.friends = without(other.friends, name);
  } else {
    return json({ error: 'Неизвестное действие' }, 400);
  }
  await Promise.all([putFriends(env, name, me), putFriends(env, otherUser.name, other)]);
  return json({ ok: true, ...me });
}

/* ---------------------------------------------------------------- статус «в сети» */
// Пишем только при изменениях и не чаще раза в 25 мин без изменений (лаунчер за этим следит).
// Запись живёт 40 мин: если лаунчер закрылся без «до свидания», статус сам пропадёт.
async function presence(env, name, body) {
  const st = body.status;
  if (!st) { await env.SKINS.delete('presence:' + key(name)); return json({ ok: true }); }
  const clean = {
    state: ['online', 'playing'].includes(st.state) ? st.state : 'online',
    instance: String(st.instance || '').slice(0, 60),
    version: String(st.version || '').slice(0, 30),
    loader: String(st.loader || '').slice(0, 20),
    room: String(st.room || '').slice(0, 120),
    share: /^[A-Z0-9]{6}$/.test(String(st.share || '')) ? st.share : '',
    code: /^[A-Z0-9]{6}$/.test(String(st.code || '')) ? st.code : '',   // код комнаты (друзьям — чтобы зайти без лишних шагов)
    p2p: !!st.p2p,                            // хозяин принимает прямые подключения
    at: Date.now(),
  };
  await env.SKINS.put('presence:' + key(name), JSON.stringify(clean), { expirationTtl: 2400 });
  return json({ ok: true });
}

/* ---------------------------------------------------------------- сборки по коду */
const CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';   // без 0/O и 1/I — чтобы не путали
async function putShare(env, name, body) {
  const text = JSON.stringify(body.pack || null);
  if (!body.pack || typeof body.pack !== 'object') return json({ error: 'Пустая сборка' }, 400);
  if (text.length > 512 * 1024) return json({ error: 'Сборка слишком большая для кода' }, 400);
  for (let attempt = 0; attempt < 5; attempt++) {
    const bytes = crypto.getRandomValues(new Uint8Array(6));
    const code = [...bytes].map((b) => CODE_CHARS[b % CODE_CHARS.length]).join('');
    if (await env.SKINS.get('share:' + code)) continue;
    await env.SKINS.put('share:' + code, JSON.stringify({ by: name, at: Date.now(), pack: body.pack }), { expirationTtl: 30 * 86400 });
    return json({ ok: true, code, days: 30 });
  }
  return json({ error: 'Не получилось придумать код, попробуйте ещё раз' }, 500);
}

async function getShare(env, code) {
  const data = await env.SKINS.get('share:' + code, 'json');
  if (!data) return json({ error: 'Код не найден или устарел (коды живут 30 дней)' }, 404);
  return json({ ok: true, ...data });
}

/* ---------------------------------------------------------------- комнаты по коду (без добавления в друзья) */
// Хозяин открыл мир «для друзей» — получает код из 6 символов. Любой, у кого есть код, видит, к кому и в какую
// версию заходить, и может подключиться напрямую (p2p с этим кодом) или по адресу e4mc. Комната живёт 6 часов
// или пока хозяин её не закроет. Записи: открыть — 1, обновить адрес — 1, закрыть — 1.
// mods — моды хозяина, без которых гостя выкинет из мира (лаунчер гостя предложит поставить только их)
const cleanRoomMods = (list) => (Array.isArray(list) ? list : []).slice(0, 40)
  .filter((m) => m && /^[a-z0-9_-]{1,64}$/.test(String(m.id || '')) && /^[0-9a-f]{40}$/.test(String(m.sha1 || '')))
  .map((m) => ({ id: String(m.id), name: String(m.name || m.id).slice(0, 60), sha1: String(m.sha1), size: Math.max(0, Math.min(2147483647, Math.floor(Number(m.size) || 0))) }));
const cleanRoomInfo = (i) => ({
  instance: String((i && i.instance) || '').slice(0, 60), version: String((i && i.version) || '').slice(0, 30),
  loader: String((i && i.loader) || '').slice(0, 20), room: String((i && i.room) || '').slice(0, 120), p2p: !!(i && i.p2p),
  mods: cleanRoomMods(i && i.mods),
});
async function room(env, name, body) {
  const action = String(body.action || '');
  const code = String(body.code || '').toUpperCase();
  if (action === 'open') {
    for (let attempt = 0; attempt < 5; attempt++) {
      const bytes = crypto.getRandomValues(new Uint8Array(6));
      const c = [...bytes].map((b) => CODE_CHARS[b % CODE_CHARS.length]).join('');
      if (await env.SKINS.get('room:' + c)) continue;
      await env.SKINS.put('room:' + c, JSON.stringify({ host: name, info: cleanRoomInfo(body.info), at: Date.now() }), { expirationTtl: 6 * 3600 });
      return json({ ok: true, code: c });
    }
    return json({ error: 'Не получилось придумать код, попробуйте ещё раз' }, 500);
  }
  if (!/^[A-Z0-9]{6}$/.test(code)) return json({ error: 'Неверный код комнаты' }, 400);
  const cur = await env.SKINS.get('room:' + code, 'json');
  if (!cur || key(cur.host) !== key(name)) return json({ ok: true, gone: true });
  if (action === 'update') {
    await env.SKINS.put('room:' + code, JSON.stringify({ ...cur, info: cleanRoomInfo(body.info) }), { expirationTtl: 6 * 3600 });
    return json({ ok: true });
  }
  if (action === 'close') { await env.SKINS.delete('room:' + code); return json({ ok: true }); }
  return json({ error: 'Неизвестное действие' }, 400);
}

async function getRoom(env, code) {
  const data = await env.SKINS.get('room:' + code, 'json');
  if (!data) return json({ error: 'Комната не найдена — она уже закрыта или код с ошибкой', code: 'gone' }, 404);
  return json({ ok: true, host: data.host, info: data.info });
}

/* ---------------------------------------------------------------- ретранслятор TURN (Cloudflare) */
// Для прямого подключения за строгим NAT: выдаём временные (на сутки) данные для TURN Cloudflare.
// Ключ TURN_KEY_ID / TURN_KEY_TOKEN — секреты воркера, в код не вшиты. Запись в KV не нужна.
async function turn(env) {
  if (!env.TURN_KEY_ID || !env.TURN_KEY_TOKEN) return json({ error: 'TURN не настроен' }, 503);
  const r = await fetch(`https://rtc.live.cloudflare.com/v1/turn/keys/${env.TURN_KEY_ID}/credentials/generate`, {
    method: 'POST',
    headers: { authorization: 'Bearer ' + env.TURN_KEY_TOKEN, 'content-type': 'application/json' },
    body: JSON.stringify({ ttl: 86400 }),
  });
  if (!r.ok) return json({ error: 'TURN недоступен (' + r.status + ')' }, 502);
  const d = await r.json().catch(() => null);
  const s = d && d.iceServers;
  if (!s) return json({ error: 'TURN вернул пустой ответ' }, 502);
  return json({ ok: true, iceServers: [s] });
}

/* ---------------------------------------------------------------- прямое соединение (обмен приглашениями) */
// Хозяин кладёт «предложение» (SDP), гость — «ответ». Каждая запись живёт 10 минут.
async function p2p(env, name, body) {
  // Прямое соединение (как в XMCL): гость кладёт «предложение» хозяину, хозяин отвечает каждому гостю.
  // Заходить можно только к друзьям. Всё живёт 10 минут. На одно подключение — 2 записи в KV.
  const action = String(body.action || '');
  const big = (v) => JSON.stringify(v || '').length > 48 * 1024;
  const okId = (v) => /^[a-z0-9]{8,24}$/.test(String(v || ''));
  if (action === 'offer') {                 // гость → хозяину
    const host = String(body.host || '');
    if (!okId(body.id) || !host || big(body.sdp)) return json({ error: 'Неверный запрос' }, 400);
    // зайти можно другу хозяина или тому, у кого есть код его комнаты (дружить не обязательно)
    const viaCode = /^[A-Z0-9]{6}$/.test(String(body.code || '')) && await env.SKINS.get('room:' + body.code, 'json');
    if (!(viaCode && key(viaCode.host) === key(host))) {
      const hostFriends = await getFriends(env, host);
      if (!has(hostFriends.friends, name)) return json({ error: `Вы не в друзьях у ${host}. Попросите у него код комнаты`, code: 'not_friend' }, 403);
    }
    // Две заявки к одному хозяину могут прийти одновременно: KV не умеет «дописать в список», и вторая запись
    // затирает первую. Поэтому после записи перечитываем список и, если нашей заявки в нём нет, дописываем снова
    // (до 4 раз, с небольшой случайной паузой, чтобы два гостя не повторяли запись в один и тот же миг).
    const inKey = 'p2p:in:' + key(host);
    const mine = { id: String(body.id), from: name, sdp: body.sdp, at: Date.now() };
    for (let attempt = 0; attempt < 4; attempt++) {
      const inbox = (await env.SKINS.get(inKey, 'json')) || [];
      const fresh = inbox.filter((o) => Date.now() - o.at < 600000 && o.id !== mine.id).slice(-9);
      fresh.push(mine);
      await env.SKINS.put(inKey, JSON.stringify(fresh), { expirationTtl: 600 });
      await new Promise((r) => setTimeout(r, 120 + Math.random() * 180));
      const check = (await env.SKINS.get(inKey, 'json')) || [];
      if (check.some((o) => o.id === mine.id)) return json({ ok: true });
    }
    return json({ error: 'Сервер занят, повторите через несколько секунд' }, 503);
  }
  if (action === 'inbox') {                 // хозяин забирает предложения
    const inbox = (await env.SKINS.get('p2p:in:' + key(name), { type: 'json', cacheTtl: 30 })) || [];   // 30 с — минимум кэша KV (по умолчанию 60)
    return json({ ok: true, offers: inbox.filter((o) => Date.now() - o.at < 600000) });
  }
  if (action === 'answer') {                // хозяин → гостю
    if (!okId(body.id) || big(body.sdp)) return json({ error: 'Неверный запрос' }, 400);
    await env.SKINS.put(`p2p:ans:${key(name)}:${body.id}`, JSON.stringify({ sdp: body.sdp || null, error: String(body.error || '').slice(0, 200), at: Date.now() }), { expirationTtl: 600 });
    return json({ ok: true });
  }
  if (action === 'poll') {                  // гость ждёт ответа
    const host = String(body.host || '');
    if (!okId(body.id) || !host) return json({ error: 'Неверный запрос' }, 400);
    const a = await env.SKINS.get(`p2p:ans:${key(host)}:${body.id}`, { type: 'json', cacheTtl: 30 });
    return json({ ok: true, answer: a || null });
  }
  return json({ error: 'Неизвестное действие' }, 400);
}
