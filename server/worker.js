/*
 * Сервер скинов VaultMC (Cloudflare Workers + KV + Durable Object, бесплатный тариф).
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
 *   GET  /api/status                                         — проверка, что сервер работает (+ worlds: есть ли облако)
 *   POST /api/worlds, PUT /api/worlds/part, POST /api/worlds/file — облако миров (server/worlds.js, версия 7)
 *
 * Версия 6: где что хранится.
 *   KV (SKINS)          — ники и их ключи, скины и плащи, сборки по коду. Пишутся редко, читаются часто (игрой).
 *   Durable Object      — друзья, статусы «в сети», комнаты, приглашения P2P. Все запросы к нему идут строго
 *   (SOCIAL, «Social»)    по очереди, поэтому две одновременные заявки больше не затирают друг друга, а записей
 *                         на бесплатном тарифе в 100 раз больше, чем у KV (100 000 в сутки против 1000).
 *   Друзья из KV переносятся в Durable Object сами, при первом обращении игрока. Пока идёт переезд,
 *   списки друзей дублируются в KV — если новый сервер придётся откатить, друзья не потеряются.
 *
 * Ники (с версии 5): свободный ник занимает первый, кто пришёл. У ника до 5 ключей — один игрок на нескольких ПК
 * (ключ оффлайн-аккаунта лаунчер получает из ника и пароля, поэтому он одинаковый на всех компьютерах).
 * Занятый ник может забрать только владелец аккаунта Ely.by с этим ником (лаунчер делает «вход на сервер»
 * через Ely.by, а мы проверяем его через session/hasJoined).
 *
 * Защита (версия 6): лимиты на неверный пароль, регистрацию, загрузку скинов, коды сборок, ретранслятор и
 * поиск комнат. Счётчики живут в памяти Durable Object — на запись в хранилище они не тратятся.
 */

import { W, WORLD_SCHEMA, worldMethods, worldPartRoute, worldFileStream, disabled as worldsDisabled } from './worlds.js';

const NAME_RE = /^[A-Za-z0-9_]{3,16}$/;
const MAX_PNG = 256 * 1024;
const CODE_RE = /^[A-Z0-9]{6}$/;
const MIN = 60 * 1000;

// Маршрут POST → действие. Социальные выполняет Durable Object, остальные — сам воркер (KV) после проверки лимитов.
const ROUTES = {
  '/api/upload': 'upload', '/api/remove': 'remove', '/api/share': 'share', '/api/turn': 'turn',
  '/api/friends': 'friends', '/api/presence': 'presence', '/api/p2p': 'p2p', '/api/room': 'room',
  '/api/recovery': 'recovery', '/api/rename': 'rename', '/api/worlds': 'worlds', '/api/worlds/file': 'worldsFile',
};
const SOCIAL_ROUTES = new Set(['friends', 'presence', 'p2p', 'room', 'worlds']);

/* Порог версии лаунчера (9.0.0): старые лаунчеры уже не умеют то, на что рассчитан сервер (ретранслятор только с ключом,
 * поиск комнаты от имени игрока). Вместо молчаливой поломки — понятный отказ с кодом update_required.
 * Версию лаунчер сообщает в User-Agent «VaultMC/9.0.0»; запрос без такого заголовка (сайт, тесты) пропускаем. */
const MIN_LAUNCHER = [9, 0, 0];
const UPDATE_GATED = (route, body) => (route === 'room' && (body.action === 'open' || body.action === 'lookup'))
  || (route === 'p2p' && body.action === 'offer');

export function launcherTooOld(request, route, body) {
  if (!UPDATE_GATED(route, body || {})) return null;
  const m = /^VaultMC\/(\d+)\.(\d+)\.(\d+)/.exec(request.headers.get('User-Agent') || '');
  if (!m) return null;
  const v = [+m[1], +m[2], +m[3]];
  for (let i = 0; i < 3; i++) {
    if (v[i] > MIN_LAUNCHER[i]) return null;
    if (v[i] < MIN_LAUNCHER[i]) return json({ error: 'Обновите лаунчер: эта версия больше не поддерживается сервером', code: 'update_required' }, 426);
  }
  return null;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    let path;
    try { path = decodeURIComponent(url.pathname); } catch { return new Response('Not found', { status: 404 }); }
    const ip = request.headers.get('CF-Connecting-IP') || '';
    try {
      if (request.method === 'GET') {
        const m = path.match(/^\/(skins|capes)\/([A-Za-z0-9_]{1,16})\.png$/);
        if (m) return texture(env, m[1] === 'skins' ? 'skin' : 'cape', m[2]);
        const cp = path.match(/^\/csl\/([A-Za-z0-9_]{1,16})\.json$/);
        if (cp) return cslProfile(env, cp[1]);
        const ct = path.match(/^\/(?:csl\/)?textures\/([0-9a-f]{40})([sc])([a-z0-9_]{1,16})$/);
        if (ct) return cslTexture(env, ct[1], ct[2] === 's' ? 'skin' : 'cape', ct[3]);
        if (path === '/api/stats') return statsRead(env, request);
        if (path.startsWith('/api/cf/')) return cfProxy(env, request, url, path.slice('/api/cf/'.length), ip);
        if (path === '/api/status' || path === '/') {
          return json({ ok: true, service: 'VaultMC skins', version: 7,
            worlds: env.WORLDS ? { v: 1, max_world: W.MAX_WORLD, part_size: W.PART } : null });
        }
        if (path === '/api/catalog') return catalogList(env);
        const sh = path.match(/^\/api\/share\/([A-Z0-9]{6})$/);
        if (sh) return url.searchParams.has('brief') ? shareBrief(env, sh[1]) : getShare(env, sh[1]);
        return new Response('Not found', { status: 404 });
      }
      if (request.method === 'PUT' && path === '/api/worlds/part') return worldPartRoute(env, request, ip, social);
      if (request.method !== 'POST') return json({ error: 'Метод не поддерживается' }, 405);
      if (path.startsWith('/api/cf/')) return cfProxy(env, request, url, path.slice('/api/cf/'.length), ip);
      const body = await request.json().catch(() => null);
      if (!body || typeof body !== 'object') return json({ error: 'Пустой запрос' }, 400);
      if (path === '/api/stat') return social(env, { op: 'stat', ip, body });   // анонимная статистика падений (только с согласия игрока)
      if (path === '/api/register') return register(env, body, () => gate(env, ip), ip);
      const route = ROUTES[path];
      if (!route) return json({ error: 'Неизвестный адрес' }, 404);
      if ((route === 'worlds' || route === 'worldsFile') && !env.WORLDS) return worldsDisabled();
      const tooOld = launcherTooOld(request, route, body);
      if (tooOld) return tooOld;
      const who = await auth(env, body);
      // Один запрос в Durable Object: он проверяет лимиты (в том числе блокировку после неверных паролей)
      // и сразу выполняет социальное действие. Для действий с KV он только разрешает или запрещает.
      const res = await social(env, {
        op: 'call', route, ip, nick: String(body.name || ''), who: who.name || '',
        authError: who.error ? { error: who.error, code: who.code } : null, fail: !!who.mismatch,
        body: SOCIAL_ROUTES.has(route) || route === 'worldsFile' ? body : null, owner: who.owner || '',
      });
      if (SOCIAL_ROUTES.has(route) || res.status !== 200) return res;
      if (route === 'worldsFile') return worldFileStream(env, await res.json(), body);
      if (route === 'upload') return upload(env, who.name, body);
      if (route === 'remove') return remove(env, who.name, body);
      if (route === 'share') return putShare(env, who.name, body);
      if (route === 'recovery') return setRecovery(env, who.name, body);
      if (route === 'rename') return renameNick(env, who.name, body);
      return turn(env);
    } catch (err) {
      // подробности — только в журнал воркера, игроку — общий текст (раньше наружу уходило сообщение об ошибке)
      console.error('worker', path, err);
      return json({ error: 'Ошибка сервера, попробуйте ещё раз' }, 500);
    }
  },
};

/* Прокси к каталогу CurseForge: ключ разработчика лежит в секрете CF_KEY (npx wrangler secret put CF_KEY),
 * игроки его не видят. Пускаем только то, что нужно лаунчеру: поиск, файлы модов, отпечатки, ссылки на скачивание.
 * Лимит — 120 запросов в минуту с одного адреса (счётчики в памяти этого экземпляра воркера). */
const CF_API = 'https://api.curseforge.com/v1/';
const CF_ALLOWED = /^(games\/432|mods\/search|mods\/\d{1,9}(\/files(\/\d{1,9}(\/changelog)?)?)?|fingerprints\/432)$/;
const cfHits = new Map();

function cfAllowed(ip) {
  const now = Date.now();
  const recent = (cfHits.get(ip) || []).filter((t) => now - t < MIN);
  if (recent.length >= 120) { cfHits.set(ip, recent); return false; }
  recent.push(now);
  cfHits.set(ip, recent);
  if (cfHits.size > 5000) cfHits.clear();
  return true;
}

async function cfProxy(env, request, url, rest, ip) {
  if (!env.CF_KEY) return json({ error: 'Каталог CurseForge на сервере не настроен' }, 503);
  if (!CF_ALLOWED.test(rest)) return json({ error: 'Такой запрос к каталогу не разрешён' }, 404);
  if (!cfAllowed(ip)) return tooMany('Слишком много запросов к каталогу, подождите минуту');
  const init = { method: request.method, headers: { 'x-api-key': env.CF_KEY, accept: 'application/json' } };
  if (request.method === 'POST') {
    init.body = await request.text();
    init.headers['content-type'] = 'application/json';
  }
  const res = await fetch(CF_API + rest + url.search, init);
  return new Response(res.body, {
    status: res.status,
    headers: {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': request.method === 'GET' ? 'public, max-age=300' : 'no-store',
    },
  });
}

// Чтение сводки: только с секретом STATS_KEY (npx wrangler secret put STATS_KEY); без секрета сводка закрыта.
async function statsRead(env, request) {
  const k = String(env.STATS_KEY || '');
  if (k.length < 12 || request.headers.get('x-stats-key') !== k) return new Response('Not found', { status: 404 });
  return social(env, { op: 'statsList' });
}

/* ---------------------------------------------------------------- помощники */
const json = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
});
const tooMany = (error) => json({ error, code: 'limit' }, 429);
const key = (name) => name.toLowerCase();
const CODE_CHARS = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';   // без 0/O и 1/I — чтобы не путали
const newCode = () => [...crypto.getRandomValues(new Uint8Array(6))].map((b) => CODE_CHARS[b % CODE_CHARS.length]).join('');

async function sha256(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function social(env, msg) {
  const stub = env.SOCIAL.get(env.SOCIAL.idFromName('main'));
  return stub.fetch('https://social/', { method: 'POST', body: JSON.stringify(msg) });
}

// Разрешение на запись нового ника или ключа (лимит на адрес). null — можно, иначе готовый ответ-отказ.
async function gate(env, ip) {
  const res = await social(env, { op: 'gate', ip });
  return res.status === 200 ? null : res;
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
  if (!userHashes(user).includes(await sha256(token))) return { error: TAKEN(name), code: 'auth', mismatch: true };
  return { name: user.name, owner: await ownerOf(user) };
}

/* Владелец облачных миров (docs/world-sync-design.md, раздел 4). Миры привязаны к нему, а не к нику:
 *   оффлайн — поле owner, а у старых записей — вычисленное из ника и времени создания (захват ника меняет created);
 *   Ely.by — UUID аккаунта Ely.by; у старой записи без elyId владельца нет (облако просит подтвердить Ely.by). */
async function ownerOf(user) {
  if (user.ely) return user.elyId ? 'e:' + user.elyId : null;
  if (typeof user.owner === 'string' && /^o:[0-9a-f]{32}$/.test(user.owner)) return user.owner;
  return 'o:' + (await sha256('vmc-owner:' + key(user.name) + ':' + user.created)).slice(0, 32);
}
// поля владельца для записи user: — их обязана сохранять каждая перезапись
async function ownerFields(user) {
  const owner = await ownerOf(user);
  return { ...(owner && owner.startsWith('o:') ? { owner } : {}), ...(user.elyId ? { elyId: user.elyId } : {}) };
}
// Прежний оффлайн-владелец потерял ник: его облачные миры — к удалению через 7 дней. Не прошло — уберёт очистка.
async function orphanWorlds(env, prev) {
  if (!prev || !prev.startsWith('o:')) return;
  try { await social(env, { op: 'worldsOrphan', owner: prev }); } catch (err) { console.error('worlds orphan', err); }
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

/* ---------------------------------------------------------------- скины (KV) */
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

/* Доказательство владения ником Ely.by: лаунчер сделал session/join с этим serverId.
 * Ответ — null или {id}: id — UUID аккаунта Ely.by (32 hex) или '' если Ely.by его не прислал. */
async function elybyProven(name, serverId) {
  if (!serverId || !/^[A-Za-z0-9_-]{8,64}$/.test(String(serverId))) return null;
  try {
    const r = await fetch(`https://authserver.ely.by/session/hasJoined?username=${encodeURIComponent(name)}&serverId=${encodeURIComponent(serverId)}`);
    if (r.status !== 200) return null;
    const p = await r.json().catch(() => null);
    if (!p || String(p.name || '').toLowerCase() !== name.toLowerCase()) return null;
    const id = String(p.id || '').replace(/-/g, '').toLowerCase();
    return { id: /^[0-9a-f]{32}$/.test(id) ? id : '' };
  } catch { return null; }
}

/* ---------------------------------------------------------------- код восстановления и смена ника */
// Код восстановления — 16 знаков (80 бит), его придумывает лаунчер и показывает игроку один раз.
// Сервер хранит только SHA-256 кода. Код одноразовый: после сброса пароля его нет, лаунчер выдаёт новый.
const RECOVERY_RE = new RegExp(`^[${CODE_CHARS}]{16}$`);
const normCode = (v) => String(v || '').toUpperCase().replace(/[^A-Z0-9]/g, '');
const recoveryHash = (code) => sha256('vaultmc-recovery:' + code);

async function setRecovery(env, name, body) {
  const code = normCode(body.code);
  if (!RECOVERY_RE.test(code)) return json({ error: 'Неверный код восстановления' }, 400);
  const user = await getUser(env, name);
  if (!user) return json({ error: 'Ник не зарегистрирован на сервере VaultMC', code: 'auth' }, 403);
  if (user.ely) return json({ error: 'У аккаунта Ely.by пароль восстанавливается на сайте Ely.by' }, 400);
  await env.SKINS.put('user:' + key(name), JSON.stringify({ ...user, ...(await ownerFields(user)), recovery: await recoveryHash(code) }));
  return json({ ok: true });
}

// Сброс пароля по коду: у ника остаётся только новый ключ (все старые перестают работать), код погашен.
async function recoverNick(env, existing, name, hash, body, ip) {
  const code = normCode(body.recovery);
  const ok = !!existing.recovery && RECOVERY_RE.test(code) && (await recoveryHash(code)) === existing.recovery;
  const res = await social(env, { op: 'recovery', ip, nick: name, ok });   // блокировка подбора — как у пароля
  if (res.status !== 200) return res;
  await env.SKINS.put('user:' + key(name), JSON.stringify({ name: existing.name, hash, hashes: [hash], ely: false, created: existing.created,
    ...(await ownerFields(existing)) }));
  return json({ ok: true, recovered: true });
}

// Смена ника (оффлайн-аккаунт): друзья, скин, плащ и код восстановления переезжают, старый ник освобождается.
// Ключ нового ника (из пароля и нового ника) — единственный; на других ПК нужно войти заново.
// Порядок: сначала друзья (Durable Object), потом записи KV — если что-то оборвётся, повтор пройдёт.
async function renameNick(env, name, body) {
  const to = String(body.new || '');
  const token = String(body.newToken || '');
  if (!NAME_RE.test(to)) return json({ error: 'Ник: 3–16 символов, латиница, цифры и _' }, 400);
  if (token.length < 32) return json({ error: 'Слишком короткий ключ' }, 400);
  const user = await getUser(env, name);
  if (!user) return json({ error: 'Ник не зарегистрирован на сервере VaultMC', code: 'auth' }, 403);
  if (user.ely) return json({ error: 'Ник аккаунта Ely.by меняется на сайте Ely.by' }, 400);
  const same = key(to) === key(name);
  if (!same && await getUser(env, to)) return json({ error: `Ник ${to} уже занят в VaultMC`, code: 'taken' }, 409);
  const res = await social(env, { op: 'renameSocial', from: user.name, to });
  if (res.status !== 200) return res;
  if (!same) {
    for (const kind of ['skin', 'cape']) {
      const v = await env.SKINS.get(kind + ':' + key(name));
      if (v) await env.SKINS.put(kind + ':' + key(to), v);
    }
  }
  const hash = await sha256(token);
  // владелец облачных миров — тот же (новый ник дал бы по формуле другого, поэтому поле обязательно)
  await env.SKINS.put('user:' + key(to), JSON.stringify({ name: to, hash, hashes: [hash], ely: false, created: user.created,
    ...(await ownerFields(user)), ...(user.recovery ? { recovery: user.recovery } : {}) }));
  if (!same) {
    await env.SKINS.delete('user:' + key(name));
    await Promise.all(['skin', 'cape'].map((k) => env.SKINS.delete(k + ':' + key(name))));
  }
  return json({ ok: true, name: to });
}

// allow() вызывается прямо перед записью в KV: повторный вход с тем же ключом (каждый запуск лаунчера)
// в лимит не считается, считаются только новые ники и смена ключа.
async function register(env, body, allow, ip = '') {
  const name = String(body.name || '');
  const token = String(body.token || '');
  if (!NAME_RE.test(name)) return json({ error: 'Ник: 3–16 символов, латиница, цифры и _' }, 400);
  if (token.length < 32) return json({ error: 'Слишком короткий ключ' }, 400);
  const existing = await getUser(env, name);
  const hash = await sha256(token);
  const hashes = existing ? userHashes(existing) : [];
  // bind — привязка UUID Ely.by к старой записи (облако миров просит её кодом ely_bind). Проверяется до «ключ уже
  // есть», иначе подтверждение не дошло бы до записи.
  const bind = !!(existing && existing.ely && !existing.elyId && body.ely && body.bind);
  if (hashes.includes(hash) && !bind) return json({ ok: true, already: true, ely: !!existing.ely });
  // подбор кода закрывает та же блокировка, что и подбор пароля (20 неверных за 10 минут с адреса)
  if (existing && body.recovery !== undefined) return recoverNick(env, existing, name, hash, body, ip);
  // Свободный ник занимает первый, кто пришёл — даже если такой ник есть на Ely.by (раньше оффлайн-игрока
  // с таким ником сервер не пускал совсем, и у него не работали друзья и комнаты).
  if (existing) {
    // Ник занят. Добавить свой ключ можно двумя способами:
    //  old — у лаунчера есть прежний ключ этого ника (обновление ключа на «ключ из пароля», тот же игрок);
    //  ely — игрок доказал, что это его аккаунт Ely.by. Если ник до этого держал оффлайн-игрок — он его теряет.
    const oldOk = body.old && String(body.old).length >= 32 && hashes.includes(await sha256(String(body.old)));
    const proof = !oldOk && body.ely ? await elybyProven(name, body.ely) : null;
    if (!oldOk && !proof) return json({ error: TAKEN(existing.name), code: 'taken' }, 409);
    const denied = await allow();
    if (denied) return denied;
    const prevOwner = await ownerOf(existing);
    let rec;
    if (oldOk) {
      // тот же игрок; replace — сменил пароль: прежние ключи (в том числе старый пароль) больше не действуют.
      // Код восстановления остаётся только у того же игрока, а не у нового владельца ника.
      const keep = body.replace ? [] : hashes;
      rec = { name: existing.name, hash, hashes: [...keep, hash].slice(-5), ely: !!existing.ely, created: existing.created,
        ...(await ownerFields(existing)), ...(existing.recovery ? { recovery: existing.recovery } : {}) };
    } else if (existing.ely && (existing.elyId ? proof.id === existing.elyId : !proof.id)) {
      // тот же аккаунт Ely.by (другой ПК того же игрока) — ключ добавляется. Ely.by не прислал UUID, а у записи
      // его и не было — как раньше; облако у такой записи не работает.
      rec = { name: existing.name, hash, hashes: [...hashes, hash].slice(-5), ely: true, created: existing.created,
        ...(existing.elyId ? { elyId: existing.elyId } : {}) };
    } else {
      // Ник забирают: оффлайн-ник — владелец Ely.by; имя на Ely.by сменило хозяина (другой UUID); старая запись
      // Ely.by без UUID — в ней могут лежать ключи и прежнего хозяина имени. Остаётся один ключ, владелец новый.
      rec = { name: existing.name, hash, hashes: [hash], ely: true, created: Date.now(), ...(proof.id ? { elyId: proof.id } : {}) };
    }
    await env.SKINS.put('user:' + key(name), JSON.stringify(rec));
    const nowOwner = await ownerOf(rec);
    if (prevOwner && prevOwner !== nowOwner) await orphanWorlds(env, prevOwner);
    return json({ ok: true, ely: rec.ely, cloud: !!nowOwner });
  }
  const denied = await allow();
  if (denied) return denied;
  const proof = body.ely ? await elybyProven(name, body.ely) : null;
  const rec = { name, hash, hashes: [hash], ely: !!proof, created: Date.now(), ...(proof && proof.id ? { elyId: proof.id } : {}) };
  if (!rec.ely) rec.owner = await ownerOf(rec);   // по той же формуле, что у старых записей (раздел 4.2)
  await env.SKINS.put('user:' + key(name), JSON.stringify(rec));
  return json({ ok: true, ely: rec.ely, cloud: !!(await ownerOf(rec)) });
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

/* ---------------------------------------------------------------- сборки по коду (KV) */
async function putShare(env, name, body) {
  const text = JSON.stringify(body.pack || null);
  if (!body.pack || typeof body.pack !== 'object') return json({ error: 'Пустая сборка' }, 400);
  if (text.length > 512 * 1024) return json({ error: 'Сборка слишком большая для кода' }, 400);
  for (let attempt = 0; attempt < 5; attempt++) {
    const code = newCode();
    if (await env.SKINS.get('share:' + code)) continue;
    const data = { by: name, at: Date.now(), pack: body.pack };
    await env.SKINS.put('share:' + code, JSON.stringify(data), { expirationTtl: 30 * 86400 });
    // В каталог на сайте — только по явному согласию (public: true); одна запись индекса на код
    if (body.public === true) await catalogAdd(env, { code, ...briefOf(data) });
    return json({ ok: true, code, days: 30, public: body.public === true });
  }
  return json({ error: 'Не получилось придумать код, попробуйте ещё раз' }, 500);
}

async function getShare(env, code) {
  const data = await env.SKINS.get('share:' + code, 'json');
  if (!data) return json({ error: 'Код не найден или устарел (коды живут 30 дней)' }, 404);
  return json({ ok: true, ...data });
}

// Для страницы сайта pack.html#КОД: только название, версия, загрузчик и число модов (без файлов сборки).
// Страница на другом адресе (сайт VaultMC), поэтому ответ разрешён любому сайту (CORS).
async function shareBrief(env, code) {
  const data = await env.SKINS.get('share:' + code, 'json');
  const cors = (res) => { res.headers.set('access-control-allow-origin', '*'); return res; };
  if (!data || !data.pack) return cors(json({ error: 'Код не найден или устарел (коды живут 30 дней)' }, 404));
  return cors(json({ ok: true, ...briefOf(data) }));
}

function briefOf(data) {
  const p = data.pack || {};
  const files = Array.isArray(p.index && p.index.files) ? p.index.files : [];
  return {
    name: String(p.name || '').slice(0, 80), loader: String(p.loader || '').slice(0, 20),
    game_version: String(p.game_version || '').slice(0, 30), by: String(data.by || '').slice(0, 16),
    mods: files.filter((f) => /^mods\//.test(String(f.path || ''))).length, at: data.at || 0,
  };
}

/* Каталог сборок на сайте (site/catalog.html): один ключ KV `catalog` — до 60 свежих записей, коды живут 30 дней.
 * Гонка двух одновременных записей может потерять одну запись каталога — сам код при этом остаётся рабочим. */
const CATALOG_MAX = 60, CATALOG_TTL = 30 * 86400 * 1000;

async function catalogAdd(env, entry) {
  const now = Date.now();
  const old = (await env.SKINS.get('catalog', 'json')) || [];
  const keep = (Array.isArray(old) ? old : []).filter((e) => e && e.code !== entry.code && now - (e.at || 0) < CATALOG_TTL);
  await env.SKINS.put('catalog', JSON.stringify([entry, ...keep].slice(0, CATALOG_MAX)));
}

async function catalogList(env) {
  const now = Date.now();
  const list = (await env.SKINS.get('catalog', 'json')) || [];
  const items = (Array.isArray(list) ? list : []).filter((e) => e && CODE_RE.test(String(e.code)) && now - (e.at || 0) < CATALOG_TTL);
  const res = json({ ok: true, items });
  res.headers.set('access-control-allow-origin', '*');
  res.headers.set('cache-control', 'public, max-age=60');
  return res;
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

/* ======================================================================================================
 * Durable Object «Social»: друзья, статусы, комнаты, приглашения P2P.
 * Один объект на весь сервер. Запросы к нему выполняются по одному, а запросы к его базе SQLite — без
 * ожидания (синхронно). Поэтому каждое действие устроено так: сначала всё, что надо подождать (чтение KV),
 * потом чтение и запись базы одним куском — между ними не может вклиниться другой запрос.
 * ==================================================================================================== */

// [сколько можно, за какое время]
const LIMITS = {
  failIp: [20, 10 * MIN],       // неверный пароль на один ник с одного адреса
  failNick: [300, 10 * MIN],    // неверный пароль на один ник со всех адресов
  register: [10, 1440 * MIN],   // новые ники и смена ключа с одного адреса
  skin: [30, 1440 * MIN],       // загрузка и удаление скинов/плащей на ник
  share: [10, 1440 * MIN],      // коды сборок на ник
  turn: [50, 1440 * MIN],       // данные ретранслятора на ник
  roomOpen: [50, 1440 * MIN],   // новые комнаты на ник
  nick: [240, MIN],             // любые запросы одного ника (лаунчер-хозяин спрашивает раз в 4 с — это 15 в минуту)
  roomLookup: [120, 10 * MIN],  // поиск комнаты по коду с одного адреса
  recovery: [10, 1440 * MIN],   // новый код восстановления на ник
  rename: [3, 1440 * MIN],      // смена ника
  friendReq: [30, 1440 * MIN],  // новые заявки в друзья с ника (защита от спама)
  block: [60, 1440 * MIN],      // блокировки с ника
  stat: [20, 1440 * MIN],       // отчёты статистики падений с одного адреса
};
const MAX_BLOCKED = 200;
const KV_BUCKET = { upload: 'skin', remove: 'skin', share: 'share', turn: 'turn', recovery: 'recovery', rename: 'rename' };

const SCHEMA = [
  'CREATE TABLE IF NOT EXISTS friends (owner TEXT NOT NULL, other_k TEXT NOT NULL, other TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY (owner, other_k))',
  'CREATE TABLE IF NOT EXISTS imported (k TEXT PRIMARY KEY)',
  'CREATE TABLE IF NOT EXISTS presence (k TEXT PRIMARY KEY, data TEXT NOT NULL, exp INTEGER NOT NULL)',
  'CREATE TABLE IF NOT EXISTS rooms (code TEXT PRIMARY KEY, host TEXT NOT NULL, host_k TEXT NOT NULL, info TEXT NOT NULL, exp INTEGER NOT NULL)',
  'CREATE TABLE IF NOT EXISTS offers (host_k TEXT NOT NULL, id TEXT NOT NULL, from_name TEXT NOT NULL, from_k TEXT NOT NULL, sdp TEXT NOT NULL, at INTEGER NOT NULL, PRIMARY KEY (host_k, id))',
  'CREATE TABLE IF NOT EXISTS answers (host_k TEXT NOT NULL, id TEXT NOT NULL, guest_k TEXT NOT NULL, data TEXT NOT NULL, exp INTEGER NOT NULL, PRIMARY KEY (host_k, id))',
  // когда игрок последний раз был в сети (для «был в сети 2 ч назад» у друзей)
  // анонимная статистика падений: день, тип проблемы, версия лаунчера, версия игры, загрузчик → сколько раз
  'CREATE TABLE IF NOT EXISTS crashstats (day TEXT NOT NULL, kind TEXT NOT NULL, app TEXT NOT NULL, mc TEXT NOT NULL, loader TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (day, kind, app, mc, loader))',
  'CREATE TABLE IF NOT EXISTS lastseen (k TEXT PRIMARY KEY, at INTEGER NOT NULL)',
];
// kind в таблице friends: friend — друзья, in/out — заявки, block — «я заблокировал этого игрока»
const LIST_OF = { friend: 'friends', in: 'incoming', out: 'outgoing', block: 'blocked' };
const P2P_TTL = 10 * MIN;

// mods — моды хозяина, без которых гостя выкинет из мира (лаунчер гостя предложит поставить только их)
const cleanRoomMods = (list) => (Array.isArray(list) ? list : []).slice(0, 40)
  .filter((m) => m && /^[a-z0-9_-]{1,64}$/.test(String(m.id || '')) && /^[0-9a-f]{40}$/.test(String(m.sha1 || '')))
  .map((m) => ({ id: String(m.id), name: String(m.name || m.id).slice(0, 60), ver: /^[0-9A-Za-z.+_-]{1,40}$/.test(String(m.ver || '')) ? String(m.ver) : '', sha1: String(m.sha1), size: Math.max(0, Math.min(2147483647, Math.floor(Number(m.size) || 0))) }));
// комната на ретрансляторе VaultMC с ключом (лаунчер 6.4.14+): порт и ключ видят только друзья и те, у кого есть код
// host — адрес ретранслятора, где открыта комната (лаунчер 7.0.35+); у старых комнат его нет, гость берёт запасной
const cleanRelay = (r) => (r && Number.isInteger(r.port) && r.port > 0 && r.port < 65536 && /^[0-9a-f]{32}$/.test(String(r.token || ''))
  ? { port: r.port, token: String(r.token), host: /^\d{1,3}(\.\d{1,3}){3}$/.test(String(r.host || '')) ? String(r.host) : null } : null);
const cleanRoomInfo = (i) => ({
  instance: String((i && i.instance) || '').slice(0, 60), version: String((i && i.version) || '').slice(0, 30),
  loader: String((i && i.loader) || '').slice(0, 20), room: String((i && i.room) || '').slice(0, 120), p2p: !!(i && i.p2p),
  mods: cleanRoomMods(i && i.mods), relay: cleanRelay(i && i.relay),
});

export class Social {
  constructor(state, env) {
    this.env = env;
    this.sql = state.storage.sql;
    this.storage = state.storage;
    this.counters = new Map();   // лимиты: живут в памяти, при выгрузке объекта сбрасываются (это допустимо)
    this.committing = new Set(); // облако миров: какие commit сейчас ждут R2 (остальные committing — от прошлого запуска)
    this.cleanedAt = 0;
    for (const q of [...SCHEMA, ...WORLD_SCHEMA]) this.sql.exec(q);
  }

  // будильник объекта — уборка облака миров (server/worlds.js)
  async alarm() {
    try { await this.worldsAlarm(); } catch (err) { console.error('alarm', err); }
  }

  // Один анонимный отчёт: только тип проблемы и версии — ни ника, ни путей, ни адреса (адрес нужен лишь для лимита в памяти).
  stat(ip, body) {
    if (this.hit('stat:' + ip, LIMITS.stat)) return tooMany('Слишком много отчётов');
    const b = body && typeof body === 'object' ? body : {};
    const kind = String(b.kind || ''), app = String(b.app || ''), mc = String(b.mc || ''), loader = String(b.loader || '');
    if (!/^[a-z0-9_-]{1,40}$/.test(kind) || !/^[0-9]{1,3}(\.[0-9]{1,3}){1,3}$/.test(app)
      || !/^[0-9a-z.+_ -]{0,30}$/i.test(mc) || !/^[a-z]{0,12}$/.test(loader)) return json({ error: 'Неверный отчёт' }, 400);
    const day = new Date().toISOString().slice(0, 10);
    this.sql.exec('INSERT INTO crashstats (day, kind, app, mc, loader, n) VALUES (?, ?, ?, ?, ?, 1) ON CONFLICT(day, kind, app, mc, loader) DO UPDATE SET n = n + 1', day, kind, app, mc, loader);
    this.sql.exec('DELETE FROM crashstats WHERE day < ?', new Date(Date.now() - 120 * 86400000).toISOString().slice(0, 10));
    return json({ ok: true });
  }

  rows(query, ...args) { return this.sql.exec(query, ...args).toArray(); }

  async fetch(request) {
    try {
      return await this.handle(await request.json());
    } catch (err) {
      console.error('social', err);
      return json({ error: 'Ошибка сервера, попробуйте ещё раз' }, 500);
    }
  }

  /* ---------------- лимиты */
  counter(bucket, windowMs) {
    const now = Date.now();
    let c = this.counters.get(bucket);
    if (!c || now - c.start >= windowMs) { c = { start: now, n: 0, win: windowMs }; this.counters.set(bucket, c); }
    return c;
  }
  // Суточные лимиты (окно от часа) лежат в SQLite (w_count): объект засыпает и забывает память, а с ней и счётчики —
  // раньше после каждого перезапуска «10 ников в сутки» начинались заново. Частые короткие (240 запросов в минуту,
  // поиск комнаты) остаются в памяти: запись в базу на каждый запрос съела бы суточный лимит записей Durable Object.
  persistent(windowMs) { return windowMs >= 60 * MIN; }
  hit(bucket, [limit, windowMs]) {
    if (this.persistent(windowMs)) return this.wBump('lim:' + bucket, windowMs) > limit;
    const c = this.counter(bucket, windowMs); c.n += 1; return c.n > limit;
  }
  reached(bucket, [limit, windowMs]) {
    if (this.persistent(windowMs)) return this.wCount('lim:' + bucket) >= limit;
    return this.counter(bucket, windowMs).n >= limit;
  }

  // Раз в 10 минут убираем протухшие статусы, комнаты, приглашения и старые счётчики.
  cleanup() {
    const now = Date.now();
    if (now - this.cleanedAt < 10 * MIN) return;
    this.cleanedAt = now;
    this.sql.exec('DELETE FROM presence WHERE exp <= ?', now);
    this.sql.exec('DELETE FROM rooms WHERE exp <= ?', now);
    this.sql.exec('DELETE FROM offers WHERE at <= ?', now - P2P_TTL);
    this.sql.exec('DELETE FROM answers WHERE exp <= ?', now);
    this.sql.exec('DELETE FROM w_count WHERE until <= ?', now);
    for (const [k, c] of this.counters) if (now - c.start >= c.win) this.counters.delete(k);
  }

  async handle(m) {
    this.cleanup();
    const ip = String(m.ip || '');
    if (m.op === 'gate') {
      return this.hit('register:' + ip, LIMITS.register)
        ? tooMany('С вашего адреса сегодня уже создано много ников. Попробуйте завтра') : json({ allow: true });
    }
    if (m.op === 'stat') return this.stat(ip, m.body);
    if (m.op === 'statsList') return json({ rows: this.rows('SELECT day, kind, app, mc, loader, n FROM crashstats ORDER BY day DESC, n DESC LIMIT 500'), worlds: this.worldsStats() });
    if (m.op === 'worldPart') return this.env.WORLDS ? this.worldPart(m) : worldsDisabled();
    if (m.op === 'worldsOrphan') return this.worldsOrphan(String(m.owner || ''));
    if (m.op === 'renameSocial') return this.renameSocial(String(m.from || ''), String(m.to || ''));
    if (!['call', 'recovery'].includes(m.op)) return json({ error: 'Неизвестный запрос' }, 400);

    // Блокировка после неверных паролей действует на все запросы этого ника с этого адреса — даже с верным
    // паролем, иначе подбор просто продолжался бы. Общий предел на ник выше, чтобы чужой не мог легко
    // заблокировать игрока со своего адреса. Неверный код восстановления считается так же.
    // Счётчики — в SQLite (w_count): в памяти они обнулялись бы, когда объект засыпает через 10 с без запросов,
    // и медленный подбор пароля в тихие часы ничем не ограничивался бы. Пишутся только при неверном пароле.
    const nk = key(String(m.nick || ''));
    const failIp = 'fail:' + nk + ':' + ip, failNick = 'fail:' + nk;
    if (this.wCount(failIp) >= LIMITS.failIp[0] || this.wCount(failNick) >= LIMITS.failNick[0]) {
      return tooMany('Слишком много попыток входа с неверным паролем. Подождите 10 минут');
    }
    const failed = () => { this.wBump(failIp, LIMITS.failIp[1]); this.wBump(failNick, LIMITS.failNick[1]); };
    if (m.op === 'recovery') {
      if (m.ok) return json({ allow: true });
      failed();
      return json({ error: 'Неверный код восстановления — проверьте, что ник и код набраны без ошибок', code: 'recovery' }, 403);
    }
    if (m.authError) {
      if (m.fail) failed();
      return json(m.authError, 403);
    }
    const name = String(m.who || '');
    if (this.hit('nick:' + key(name), LIMITS.nick)) return tooMany('Слишком частые запросы. Подождите минуту');

    const bucket = KV_BUCKET[m.route];
    if (bucket) {
      return this.hit(bucket + ':' + key(name), LIMITS[bucket]) ? tooMany('Сегодня это действие уже выполнялось слишком часто. Попробуйте завтра') : json({ allow: true });
    }
    const body = m.body && typeof m.body === 'object' ? m.body : {};
    if (m.route === 'friends') return this.friends(name, body);
    if (m.route === 'presence') return this.presence(name, body);
    if (m.route === 'p2p') return this.p2p(name, body);
    if (m.route === 'room') return this.room(name, body, ip);
    if (m.route === 'worlds' || m.route === 'worldsFile') {
      if (!this.env.WORLDS) return worldsDisabled();
      // старая запись Ely.by без UUID: лаунчер подтверждает Ely.by заново (register с ely и bind) и повторяет
      if (!m.owner) return json({ error: 'Подтвердите аккаунт Ely.by для облака миров', code: 'ely_bind' }, 403);
      return m.route === 'worlds' ? this.worldsCall(String(m.owner), name, body, ip) : this.worldFile(String(m.owner), body);
    }
    return json({ error: 'Неизвестный адрес' }, 404);
  }

  /* ---------------- друзья (взаимные) */
  // Переносит список друзей игрока из KV (сервер версии 5) в базу. Один раз на игрока.
  async importFriends(name) {
    const k = key(name);
    if (this.rows('SELECT 1 FROM imported WHERE k = ?', k).length) return;
    const data = await this.env.SKINS.get('friends:' + k, 'json');   // ошибка KV — запрос упадёт, перенесём в другой раз
    if (this.rows('SELECT 1 FROM imported WHERE k = ?', k).length) return;   // пока ждали KV, другой запрос уже перенёс
    for (const [field, kind] of [['friends', 'friend'], ['incoming', 'in'], ['outgoing', 'out']]) {
      for (const other of Array.isArray(data && data[field]) ? data[field] : []) {
        if (!NAME_RE.test(String(other))) continue;
        this.sql.exec('INSERT OR IGNORE INTO friends (owner, other_k, other, kind) VALUES (?, ?, ?, ?)', k, key(String(other)), String(other), kind);
      }
    }
    this.sql.exec('INSERT INTO imported (k) VALUES (?)', k);
  }

  lists(k) {
    const out = { friends: [], incoming: [], outgoing: [], blocked: [] };
    for (const r of this.rows('SELECT other, kind FROM friends WHERE owner = ? ORDER BY rowid', k)) {
      if (LIST_OF[r.kind]) out[LIST_OF[r.kind]].push(r.other);
    }
    return out;
  }

  relation(ownerK, otherK) {
    const r = this.rows('SELECT kind FROM friends WHERE owner = ? AND other_k = ?', ownerK, otherK)[0];
    return r ? r.kind : '';
  }

  setRelation(ownerK, otherName, kind) {
    if (kind) this.sql.exec('INSERT OR REPLACE INTO friends (owner, other_k, other, kind) VALUES (?, ?, ?, ?)', ownerK, key(otherName), otherName, kind);
    else this.sql.exec('DELETE FROM friends WHERE owner = ? AND other_k = ?', ownerK, key(otherName));
  }

  countKind(ownerK, kind) {
    return this.rows('SELECT COUNT(*) AS n FROM friends WHERE owner = ? AND kind = ?', ownerK, kind)[0].n;
  }

  async friends(name, body) {
    const me = key(name);
    const action = String(body.action || 'list');
    await this.importFriends(name);
    if (action === 'list') {
      const status = {};
      const rows = this.rows(
        "SELECT f.other AS other, p.data AS data FROM friends f JOIN presence p ON p.k = f.other_k WHERE f.owner = ? AND f.kind = 'friend' AND p.exp > ?",
        me, Date.now());
      for (const r of rows) status[r.other] = JSON.parse(r.data);
      // «был в сети»: только у друзей, которых сейчас нет в сети (если игрок скрывает статус, записи у него просто нет)
      const seen = {};
      for (const r of this.rows("SELECT f.other AS other, l.at AS at FROM friends f JOIN lastseen l ON l.k = f.other_k WHERE f.owner = ? AND f.kind = 'friend'", me)) {
        if (!status[r.other]) seen[r.other] = r.at;
      }
      return json({ ok: true, ...this.lists(me), status, seen });
    }

    const otherName = String(body.other || '');
    if (!NAME_RE.test(otherName)) return json({ error: 'Неверный ник друга' }, 400);
    if (key(otherName) === me) return json({ error: 'Нельзя добавить самого себя' }, 400);
    const otherUser = await this.env.SKINS.get('user:' + key(otherName), 'json');
    if (!otherUser) return json({ error: `Игрок ${otherName} ещё не заходил в VaultMC с этим ником` }, 404);
    const on = otherUser.name, ok = key(on);
    await this.importFriends(on);

    // дальше — без ожиданий: чтение и запись одним куском
    const mine = this.relation(me, ok), theirs = this.relation(ok, me);
    if (action === 'request') {
      if (mine === 'block') return json({ error: `Вы заблокировали ${on}. Сначала разблокируйте этого игрока`, code: 'blocked' }, 400);
      if (mine === 'friend' && theirs === 'friend') return json({ ok: true, ...this.lists(me) });
      if (theirs === 'block') {
        // Тихий отказ: заявка выглядит отправленной (так заблокированный не узнаёт о блокировке), но до адресата не доходит
        if (this.hit('freq:' + me, LIMITS.friendReq)) return tooMany('Слишком много заявок за сутки. Попробуйте завтра');
        this.setRelation(me, on, 'out');
      } else if (mine === 'in' || theirs === 'friend' || mine === 'friend') {   // он уже звал нас (или списки разошлись) — сразу дружим
        this.setRelation(me, on, 'friend');
        this.setRelation(ok, name, 'friend');
      } else {
        if (mine === 'out' && theirs === 'in') return json({ ok: true, ...this.lists(me) });
        if (this.countKind(me, 'out') > 200) return json({ error: 'Слишком много заявок' }, 429);
        // защита от спама: у одного игрока не больше 200 входящих заявок и 30 новых заявок в сутки
        if (theirs !== 'in' && this.countKind(ok, 'in') > 200) return json({ error: `У ${on} слишком много заявок` }, 429);
        if (this.hit('freq:' + me, LIMITS.friendReq)) return tooMany('Слишком много заявок за сутки. Попробуйте завтра');
        this.setRelation(me, on, 'out');
        this.setRelation(ok, name, 'in');
      }
    } else if (action === 'accept') {
      if (mine !== 'in') return json({ error: 'Заявки нет' }, 400);
      this.setRelation(me, on, 'friend');
      this.setRelation(ok, name, 'friend');
    } else if (action === 'decline' || action === 'remove') {
      // чужую блокировку (theirs === 'block') и свою (mine === 'block') эти действия не снимают — для этого есть unblock
      if (mine !== 'block') this.setRelation(me, on, null);
      if (theirs !== 'block') this.setRelation(ok, name, null);
    } else if (action === 'block') {
      if (mine !== 'block' && this.countKind(me, 'block') >= MAX_BLOCKED) return json({ error: 'Слишком много заблокированных игроков' }, 429);
      if (this.hit('block:' + me, LIMITS.block)) return tooMany('Слишком много блокировок за сутки. Попробуйте завтра');
      this.setRelation(me, on, 'block');                       // дружба и заявки в обе стороны пропадают
      if (theirs && theirs !== 'block') this.setRelation(ok, name, null);
    } else if (action === 'unblock') {
      if (mine === 'block') this.setRelation(me, on, null);
    } else {
      return json({ error: 'Неизвестное действие' }, 400);
    }
    const mineNow = this.lists(me);
    return json({ ok: true, ...mineNow });
  }

  // Смена ника: во всех списках друзей старый ник заменяется новым. Сначала переносим из KV списки самого игрока
  // и всех его друзей — иначе друг, ещё не перенесённый с сервера v5, потом принёс бы старый ник из KV, и чужой,
  // занявший освободившийся ник, получил бы его друзей.
  async renameSocial(from, to) {
    if (!NAME_RE.test(from) || !NAME_RE.test(to)) return json({ error: 'Неверный ник' }, 400);
    const fk = key(from), tk = key(to);
    await this.importFriends(from);
    for (const other of this.rows('SELECT other FROM friends WHERE owner = ?', fk).map((r) => r.other)) {
      await this.importFriends(other);
    }
    // дальше — без ожиданий
    this.sql.exec('UPDATE OR REPLACE friends SET owner = ? WHERE owner = ?', tk, fk);
    this.sql.exec('UPDATE OR REPLACE friends SET other_k = ?, other = ? WHERE other_k = ?', tk, to, fk);
    this.sql.exec('UPDATE OR REPLACE lastseen SET k = ? WHERE k = ?', tk, fk);
    this.sql.exec('INSERT OR IGNORE INTO imported (k) VALUES (?)', tk);
    this.sql.exec('DELETE FROM presence WHERE k = ?', fk);
    this.sql.exec('DELETE FROM rooms WHERE host_k = ?', fk);
    this.sql.exec('DELETE FROM offers WHERE host_k = ? OR from_k = ?', fk, fk);
    this.sql.exec('DELETE FROM answers WHERE host_k = ? OR guest_k = ?', fk, fk);
    if (fk !== tk) {
      try { await this.env.SKINS.delete('friends:' + fk); } catch (err) { console.error('rename backup', err); }
    }
    return json({ ok: true });
  }

  /* ---------------- статус «в сети» */
  // Запись живёт 40 мин: если лаунчер закрылся без «до свидания», статус сам пропадёт.
  presence(name, body) {
    const st = body.status;
    const k = key(name);
    if (!st) {
      const had = this.rows('SELECT 1 FROM presence WHERE k = ?', k).length > 0;
      this.sql.exec('DELETE FROM presence WHERE k = ?', k);
      if (had) this.touchSeen(k);                                // вышел из сети — запоминаем, когда
      return json({ ok: true });
    }
    const clean = {
      state: ['online', 'playing'].includes(st.state) ? st.state : 'online',
      instance: String(st.instance || '').slice(0, 60),
      version: String(st.version || '').slice(0, 30),
      loader: String(st.loader || '').slice(0, 20),
      room: String(st.room || '').slice(0, 120),
      share: CODE_RE.test(String(st.share || '')) ? st.share : '',
      code: CODE_RE.test(String(st.code || '')) ? st.code : '',   // код комнаты (друзьям — чтобы зайти без лишних шагов)
      p2p: !!st.p2p,                            // хозяин принимает прямые подключения
      relay: cleanRelay(st.relay),              // комната на ретрансляторе VaultMC (порт и ключ)
      at: Date.now(),
    };
    this.sql.exec('INSERT OR REPLACE INTO presence (k, data, exp) VALUES (?, ?, ?)', k, JSON.stringify(clean), Date.now() + 40 * MIN);
    this.touchSeen(k);
    return json({ ok: true });
  }

  touchSeen(k) { this.sql.exec('INSERT OR REPLACE INTO lastseen (k, at) VALUES (?, ?)', k, Date.now()); }

  /* ---------------- комнаты по коду (без добавления в друзья) */
  // Хозяин открыл мир «для друзей» — получает код из 6 символов. Любой, у кого есть код, видит, к кому и в какую
  // версию заходить, и может подключиться напрямую (p2p с этим кодом) или по адресу e4mc. Комната живёт 6 часов
  // или пока хозяин её не закроет.
  room(name, body, ip = '') {
    const action = String(body.action || '');
    const code = String(body.code || '').toUpperCase();
    const now = Date.now();
    if (action === 'lookup') {
      // Поиск комнаты от имени игрока (лаунчер 6.4.20+): заблокированный хозяином видит «комната не найдена».
      if (this.hit('lookup:' + ip, LIMITS.roomLookup)) return tooMany('Слишком много попыток найти комнату. Подождите 10 минут');
      if (!CODE_RE.test(code)) return json({ error: 'Неверный код комнаты' }, 400);
      const r = this.rows('SELECT host, host_k, info FROM rooms WHERE code = ? AND exp > ?', code, now)[0];
      if (!r || this.relation(r.host_k, key(name)) === 'block') {
        return json({ error: 'Комната не найдена — она уже закрыта или код с ошибкой', code: 'gone' }, 404);
      }
      return json({ ok: true, host: r.host, info: JSON.parse(r.info) });
    }
    if (action === 'open') {
      if (this.hit('roomOpen:' + key(name), LIMITS.roomOpen)) return tooMany('Сегодня открыто слишком много комнат. Попробуйте завтра');
      for (let attempt = 0; attempt < 5; attempt++) {
        const c = newCode();
        if (this.rows('SELECT 1 FROM rooms WHERE code = ? AND exp > ?', c, now).length) continue;
        this.sql.exec('INSERT OR REPLACE INTO rooms (code, host, host_k, info, exp) VALUES (?, ?, ?, ?, ?)',
          c, name, key(name), JSON.stringify(cleanRoomInfo(body.info)), now + 6 * 60 * MIN);
        return json({ ok: true, code: c });
      }
      return json({ error: 'Не получилось придумать код, попробуйте ещё раз' }, 500);
    }
    if (!CODE_RE.test(code)) return json({ error: 'Неверный код комнаты' }, 400);
    const cur = this.rows('SELECT host_k FROM rooms WHERE code = ? AND exp > ?', code, now)[0];
    if (!cur || cur.host_k !== key(name)) return json({ ok: true, gone: true });
    if (action === 'update') {
      this.sql.exec('UPDATE rooms SET info = ?, exp = ? WHERE code = ?', JSON.stringify(cleanRoomInfo(body.info)), now + 6 * 60 * MIN, code);
      return json({ ok: true });
    }
    if (action === 'close') { this.sql.exec('DELETE FROM rooms WHERE code = ?', code); return json({ ok: true }); }
    return json({ error: 'Неизвестное действие' }, 400);
  }

  /* ---------------- прямое соединение (обмен приглашениями) */
  // Гость кладёт «предложение» (SDP) хозяину, хозяин отвечает каждому гостю. Всё живёт 10 минут.
  // Заходить можно другу хозяина или тому, у кого есть код его комнаты.
  async p2p(name, body) {
    const action = String(body.action || '');
    const big = (v) => JSON.stringify(v || '').length > 48 * 1024;
    const okId = (v) => /^[a-z0-9]{8,24}$/.test(String(v || ''));
    const me = key(name);
    const now = Date.now();

    if (action === 'offer') {                 // гость → хозяину
      const host = String(body.host || '');
      if (!okId(body.id) || !NAME_RE.test(host) || big(body.sdp)) return json({ error: 'Неверный запрос' }, 400);
      const hk = key(host);
      const code = String(body.code || '');
      // заблокированный хозяином получает одинаковый отказ и по дружбе, и по коду комнаты — причину он не узнаёт
      if (this.relation(hk, me) === 'block') return json({ error: `${host} сейчас не принимает подключения`, code: 'closed' }, 403);
      const viaCode = CODE_RE.test(code) && this.rows('SELECT 1 FROM rooms WHERE code = ? AND host_k = ? AND exp > ?', code, hk, now).length > 0;
      if (!viaCode) {
        await this.importFriends(host);
        if (this.relation(hk, me) !== 'friend') {
          return json({ error: `Вы не в друзьях у ${host}. Попросите у него код комнаты`, code: 'not_friend' }, 403);
        }
      }
      const id = String(body.id);
      const prev = this.rows('SELECT from_k FROM offers WHERE host_k = ? AND id = ?', hk, id)[0];
      if (prev && prev.from_k !== me) return json({ error: 'Неверный запрос' }, 400);
      // гость повторяет приглашение с тем же id через 35 и 75 с — запись просто обновляется
      this.sql.exec('INSERT OR REPLACE INTO offers (host_k, id, from_name, from_k, sdp, at) VALUES (?, ?, ?, ?, ?, ?)',
        hk, id, name, me, JSON.stringify(body.sdp ?? null), now);
      // у хозяина не больше 10 свежих приглашений — лишние (самые старые) убираем
      this.sql.exec('DELETE FROM offers WHERE host_k = ? AND id NOT IN (SELECT id FROM offers WHERE host_k = ? AND at > ? ORDER BY at DESC LIMIT 10)',
        hk, hk, now - P2P_TTL);
      return json({ ok: true });
    }
    if (action === 'inbox') {                 // хозяин забирает предложения
      const offers = this.rows('SELECT id, from_name, sdp, at FROM offers WHERE host_k = ? AND at > ? ORDER BY at', me, now - P2P_TTL)
        .map((r) => ({ id: r.id, from: r.from_name, sdp: JSON.parse(r.sdp), at: r.at }));
      return json({ ok: true, offers });
    }
    if (action === 'answer') {                // хозяин → гостю (только на приглашение, которое пришло ему)
      if (!okId(body.id) || big(body.sdp)) return json({ error: 'Неверный запрос' }, 400);
      const offer = this.rows('SELECT from_k FROM offers WHERE host_k = ? AND id = ?', me, String(body.id))[0];
      if (!offer) return json({ ok: true, ignored: true });
      const data = { sdp: body.sdp || null, error: String(body.error || '').slice(0, 200), at: now };
      this.sql.exec('INSERT OR REPLACE INTO answers (host_k, id, guest_k, data, exp) VALUES (?, ?, ?, ?, ?)',
        me, String(body.id), offer.from_k, JSON.stringify(data), now + P2P_TTL);
      return json({ ok: true });
    }
    if (action === 'poll') {                  // гость ждёт ответа (видит только ответ на своё приглашение)
      const host = String(body.host || '');
      if (!okId(body.id) || !NAME_RE.test(host)) return json({ error: 'Неверный запрос' }, 400);
      const r = this.rows('SELECT data FROM answers WHERE host_k = ? AND id = ? AND guest_k = ? AND exp > ?', key(host), String(body.id), me, now)[0];
      return json({ ok: true, answer: r ? JSON.parse(r.data) : null });
    }
    return json({ error: 'Неизвестное действие' }, 400);
  }
}

Object.assign(Social.prototype, worldMethods);
