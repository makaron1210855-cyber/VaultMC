/*
 * Облако миров (этап 3 плана 8.0). Дизайн — docs/world-sync-design.md, номера разделов ниже — оттуда.
 *
 *   POST /api/worlds       {name, token, action, v: 1, …} — list | begin | commit | abort | lease | release | restore | delete
 *   PUT  /api/worlds/part  тело — байты части; заголовки X-VMC-Upload (sid), X-VMC-Part (n), X-VMC-MD5
 *   POST /api/worlds/file  {name, token, wid, rid, offset} — поток байт архива
 *
 * Файлы миров — в R2 (привязка WORLDS, бакет закрытый), метаданные — в SQLite объекта Social (все запросы к нему
 * идут по одному: «сравни и замени» без лишних механизмов). Без привязки WORLDS облако отвечает 503 disabled.
 * Мир принадлежит владельцу (owner из записи user:<ник>), а не нику: новый хозяин ника чужие миры не получит.
 * Счётчики, от которых зависят деньги и место, — в таблице w_count (объект засыпает и забывает память).
 */

const MIN = 60 * 1000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;
const MiB = 1024 * 1024;

// Пределы (раздел 10.2). Объект, а не константы — тесты уменьшают размеры.
export const W = {
  MAX_WORLD: 512 * MiB,     // архив мира
  PART: 16 * MiB,           // часть загрузки (все, кроме последней, одного размера — так требует R2)
  QUOTA: 1024 * MiB,        // на владельца: текущие + прошлые версии + незавершённые загрузки
  WORLDS: 5,                // миров на владельца
  GLOBAL: 8e9,              // на весь сервер (бесплатно 10 ГБ-месяц)
  PREV_KEEP: 3 * DAY,       // прошлая версия («если есть место» — удаляется первой)
  SESSION_IDLE: DAY,        // загрузка живёт сутки от последней части…
  SESSION_MAX: 3 * DAY,     // …но не больше 3 суток от начала
  BUSY: 10 * MIN,           // чужая загрузка «идёт», если её трогали недавно
  LEASE: 20 * MIN,          // «мир открыт на ПК X»
  HISTORY: 200,             // строк цепочки версий на мир
  INACTIVE: 90 * DAY,       // без облачных запросов → миры удаляются
  ORPHAN: 7 * DAY,          // владелец потерял ник → миры удаляются
  LIVE_PER_OWNER: 3,        // живых загрузок на владельца
  BEGIN_DAY: 40,            // созданных загрузок в сутки на владельца
  FILE_DAY: 100,            // скачиваний (с докачками) в сутки на владельца
  NEW_OWNER_DAY: 3,         // новых облачных владельцев в сутки с адреса
  PART_BAD: [50, 10 * MIN], // неверных sid с адреса
  OPS_MONTH: 800000,        // операций класса A R2 в месяц на сервер (бесплатно 1 млн)
  ALARM: HOUR,
};

const WID_RE = /^[0-9a-f]{24}$/;
const RID_RE = /^[0-9a-f]{16}$/;
const SID_RE = /^[0-9a-f]{32}$/;
const DEV_RE = /^[0-9a-f]{32}$/;
const SHA_RE = /^[0-9a-f]{64}$/;
const LIVE = "('creating', 'open', 'committing', 'aborted')";   // держат место (aborted — пока R2 не отменил загрузку)
const ACTIVE = "('creating', 'open', 'committing')";

export const WORLD_SCHEMA = [
  'CREATE TABLE IF NOT EXISTS w_worlds (owner TEXT NOT NULL, wid TEXT NOT NULL, title TEXT NOT NULL, head_rev INTEGER NOT NULL DEFAULT 0, head_rid TEXT NOT NULL DEFAULT \'\', created INTEGER NOT NULL, updated INTEGER NOT NULL, PRIMARY KEY (owner, wid))',
  'CREATE TABLE IF NOT EXISTS w_vers (owner TEXT NOT NULL, wid TEXT NOT NULL, rid TEXT NOT NULL, rev INTEGER NOT NULL, obj TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, at INTEGER NOT NULL, device TEXT NOT NULL, device_name TEXT NOT NULL, meta TEXT NOT NULL, keep_until INTEGER, PRIMARY KEY (owner, wid, rid))',
  'CREATE TABLE IF NOT EXISTS w_hist (owner TEXT NOT NULL, wid TEXT NOT NULL, rev INTEGER NOT NULL, rid TEXT NOT NULL, at INTEGER NOT NULL, device_name TEXT NOT NULL, PRIMARY KEY (owner, wid, rev))',
  'CREATE INDEX IF NOT EXISTS w_hist_rid ON w_hist (owner, wid, rid)',
  'CREATE TABLE IF NOT EXISTS w_sessions (sid TEXT PRIMARY KEY, owner TEXT NOT NULL, wid TEXT NOT NULL, base_rid TEXT NOT NULL, rid TEXT NOT NULL, obj TEXT NOT NULL, upload_id TEXT NOT NULL DEFAULT \'\', size INTEGER NOT NULL, part_size INTEGER NOT NULL, parts INTEGER NOT NULL, sha256 TEXT NOT NULL, meta TEXT NOT NULL, device TEXT NOT NULL, device_name TEXT NOT NULL, state TEXT NOT NULL, rev INTEGER, created INTEGER NOT NULL, touched INTEGER NOT NULL, exp INTEGER NOT NULL, puts INTEGER NOT NULL DEFAULT 0)',
  'CREATE TABLE IF NOT EXISTS w_leases (owner TEXT NOT NULL, wid TEXT NOT NULL, device TEXT NOT NULL, device_name TEXT NOT NULL, until INTEGER NOT NULL, PRIMARY KEY (owner, wid, device))',
  'CREATE TABLE IF NOT EXISTS w_owners (owner TEXT PRIMARY KEY, nick TEXT NOT NULL, last_active INTEGER NOT NULL, orphaned_at INTEGER)',
  'CREATE TABLE IF NOT EXISTS w_trash (obj TEXT PRIMARY KEY, at INTEGER NOT NULL)',
  'CREATE TABLE IF NOT EXISTS w_count (k TEXT PRIMARY KEY, n INTEGER NOT NULL, until INTEGER NOT NULL)',
  'CREATE TABLE IF NOT EXISTS w_kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)',
];

/* ---------------------------------------------------------------- помощники */
const wj = (data, status = 200) => new Response(JSON.stringify(data), {
  status, headers: { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' },
});
const werr = (status, code, error, extra = {}) => wj({ error, code, ...extra }, status);
export const disabled = () => werr(503, 'disabled', 'Облако миров на этом сервере выключено');
const bad = (what = 'Неверный запрос') => werr(400, 'bad', what);
const noSession = () => werr(404, 'session', 'Загрузка не найдена или устарела — начните отправку заново');
const gone = () => werr(404, 'gone', 'Облачной копии этого мира нет');
const full = () => werr(507, 'full', 'Облако VaultMC сейчас заполнено. Мир остался на этом компьютере — попробуйте позже');
const limited = (error) => werr(429, 'limit', error);

export async function sha256hex(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}
const rhex = (len) => [...crypto.getRandomValues(new Uint8Array(len / 2))].map((b) => b.toString(16).padStart(2, '0')).join('');
const str = (v, n) => String(v ?? '').replace(/[\u0000-\u001f\u007f]/g, '').slice(0, n);
const int = (v) => { const x = Math.floor(Number(v)); return Number.isFinite(x) && x >= 0 ? Math.min(x, Number.MAX_SAFE_INTEGER) : 0; };
const today = () => new Date(Date.now()).toISOString().slice(0, 10);
const month = () => new Date(Date.now()).toISOString().slice(0, 7);

// meta версии (раздел 5.2): лишнее и кривое отбрасываем
export function cleanMeta(m) {
  const o = m && typeof m === 'object' ? m : {};
  const mods = (Array.isArray(o.mods) ? o.mods : []).slice(0, 200)
    .filter((x) => x && /^[A-Za-z0-9_.-]{1,64}$/.test(String(x.id || '')))
    .map((x) => ({ id: String(x.id), v: str(x.v, 40) }));
  const icon = typeof o.icon === 'string' && o.icon.length <= 16384 && /^[A-Za-z0-9+/=]*$/.test(o.icon) ? o.icon : '';
  return {
    title: str(o.title, 64), folder: str(o.folder, 64), mc: str(o.mc, 30), data_version: int(o.data_version),
    mode: str(o.mode, 20), hardcore: !!o.hardcore, last_played: int(o.last_played), raw_size: int(o.raw_size),
    files: int(o.files), loader: str(o.loader, 20), instance: str(o.instance, 60), mods, icon,
  };
}
const devName = (v) => str(v, 40).trim() || 'ПК';

/* ---------------------------------------------------------------- маршруты воркера (данные идут мимо объекта) */
// Часть архива: правом служит sid (ключа ника здесь нет — заголовки попадают в журнал воркера).
export async function worldPartRoute(env, request, ip, social) {
  if (!env.WORLDS) return disabled();
  const sid = String(request.headers.get('x-vmc-upload') || '');
  const n = Number(request.headers.get('x-vmc-part'));
  const md5 = String(request.headers.get('x-vmc-md5') || '').toLowerCase();
  const len = Number(request.headers.get('content-length'));
  if (!Number.isInteger(len) || len < 1) return werr(400, 'part', 'Нужна длина части (Content-Length)');
  if (md5 && !/^[0-9a-f]{32}$/.test(md5)) return werr(400, 'part', 'Неверная контрольная сумма части');
  const res = await social(env, { op: 'worldPart', ip, sid, n, len });
  if (res.status !== 200) return res;
  const { obj, upload_id: uploadId } = await res.json();
  let part;
  try {
    part = await env.WORLDS.resumeMultipartUpload(obj, uploadId).uploadPart(n, request.body);
  } catch (err) {
    console.error('world part', err);
    return werr(400, 'part', 'Часть не принята — отправьте её ещё раз');
  }
  // etag части в R2 = MD5 её содержимого (кавычки — на всякий случай убираем). Не похож на MD5 — не сверяем.
  const etag = String((part && part.etag) || '').replace(/"/g, '').toLowerCase();
  if (md5 && /^[0-9a-f]{32}$/.test(etag) && etag !== md5) return werr(400, 'part', 'Часть дошла с ошибкой — отправьте её ещё раз');
  return wj({ ok: true, n, etag: part.etag });
}

// Скачивание: объект уже проверил права и выдал obj; докачка — через offset.
export async function worldFileStream(env, info, body) {
  const offset = body.offset === undefined ? 0 : Number(body.offset);
  if (!Number.isInteger(offset) || offset < 0 || offset > info.size) return bad('Неверное смещение');
  const headers = {
    'content-type': 'application/zip', 'cache-control': 'no-store',
    'x-vmc-size': String(info.size), 'x-vmc-sha256': info.sha256,
  };
  if (offset === info.size) return new Response(new Uint8Array(0), { status: 200, headers });
  const o = await env.WORLDS.get(info.obj, offset ? { range: { offset } } : undefined);
  if (!o || !o.body) return werr(410, 'missing', 'Облачная копия мира повреждена. Мир на этом ПК цел');
  return new Response(o.body, { status: 200, headers });   // длину ставит сам Workers: у тела R2 она известна
}

/* ---------------------------------------------------------------- методы объекта Social */
// Подмешиваются в Social (Object.assign(Social.prototype, worldMethods)): this.sql, this.rows, this.env, this.storage.
export const worldMethods = {
  /* -------- постоянные счётчики */
  wCount(k) {
    const r = this.rows('SELECT n FROM w_count WHERE k = ? AND until > ?', k, Date.now())[0];
    return r ? r.n : 0;
  },
  wBump(k, windowMs) {
    const now = Date.now();
    const r = this.rows('SELECT n, until FROM w_count WHERE k = ?', k)[0];
    if (!r || r.until <= now) { this.sql.exec('INSERT OR REPLACE INTO w_count (k, n, until) VALUES (?, 1, ?)', k, now + windowMs); return 1; }
    this.sql.exec('UPDATE w_count SET n = n + 1 WHERE k = ?', k);
    return r.n + 1;
  },
  opsFull() { return this.wCount('ops:' + month()) >= W.OPS_MONTH; },
  opsAdd(n = 1) { for (let i = 0; i < n; i++) this.wBump('ops:' + month(), 40 * DAY); },

  async ensureAlarm() {
    const st = this.storage;
    if (!st || typeof st.setAlarm !== 'function') return;
    try {
      if ((await st.getAlarm()) == null) await st.setAlarm(Date.now() + W.ALARM);
    } catch (err) { console.error('world alarm set', err); }
  },

  /* -------- место */
  ownerUsed(owner, exclude = []) {
    const v = this.rows('SELECT COALESCE(SUM(size), 0) AS s FROM (SELECT obj, MAX(size) AS size FROM w_vers WHERE owner = ? GROUP BY obj)', owner)[0].s;
    const s = this.rows(`SELECT sid, size FROM w_sessions WHERE owner = ? AND state IN ${LIVE}`, owner)
      .filter((r) => !exclude.includes(r.sid)).reduce((a, r) => a + r.size, 0);
    return v + s;
  },
  globalUsed(exclude = []) {
    const v = this.rows('SELECT COALESCE(SUM(size), 0) AS s FROM (SELECT obj, MAX(size) AS size FROM w_vers GROUP BY obj)')[0].s;
    const s = this.rows(`SELECT sid, size FROM w_sessions WHERE state IN ${LIVE}`)
      .filter((r) => !exclude.includes(r.sid)).reduce((a, r) => a + r.size, 0);
    return v + s;
  },
  prevRows(owner) {
    return owner
      ? this.rows('SELECT * FROM w_vers WHERE owner = ? AND keep_until IS NOT NULL ORDER BY keep_until, at', owner)
      : this.rows('SELECT * FROM w_vers WHERE keep_until IS NOT NULL ORDER BY keep_until, at');
  },
  // Строку версии убираем, файл — в корзину, только если на него больше никто не ссылается (общее правило, 6.6).
  dropVersion(r) {
    this.sql.exec('DELETE FROM w_vers WHERE owner = ? AND wid = ? AND rid = ?', r.owner, r.wid, r.rid);
    this.trashIfFree(r.obj);
  },
  trashIfFree(obj) {
    if (this.rows('SELECT 1 FROM w_vers WHERE obj = ?', obj).length) return;
    if (this.rows(`SELECT 1 FROM w_sessions WHERE obj = ? AND state IN ${ACTIVE}`, obj).length) return;
    this.sql.exec('INSERT OR IGNORE INTO w_trash (obj, at) VALUES (?, ?)', obj, Date.now());
  },
  // Освободить место под need байт: сначала прошлые версии игрока, потом самые старые прошлые версии сервера.
  // Сначала проверяем, что место вообще найдётся — иначе ничего не удаляем.
  makeRoom(owner, need, exclude) {
    const sum = (rows) => rows.reduce((a, r) => a + r.size, 0);
    const own = this.ownerUsed(owner, exclude), ownPrev = sum(this.prevRows(owner));
    if (own - ownPrev + need > W.QUOTA) return werr(507, 'quota', 'В облаке не хватает места — удалите из облака ненужный мир', { used: own, quota: W.QUOTA });
    const glob = this.globalUsed(exclude), globPrev = sum(this.prevRows(null));
    if (glob - globPrev + need > W.GLOBAL) return full();
    for (const r of this.prevRows(owner)) {
      if (this.ownerUsed(owner, exclude) + need <= W.QUOTA) break;
      this.dropVersion(r);
    }
    for (const r of this.prevRows(null)) {
      if (this.globalUsed(exclude) + need <= W.GLOBAL) break;
      this.dropVersion(r);
    }
    return null;
  },

  /* -------- общее */
  headInfo(owner, wid) {
    const r = this.rows('SELECT rev, rid, at, size, sha256, device_name, meta FROM w_vers WHERE owner = ? AND wid = ? AND keep_until IS NULL', owner, wid)[0];
    return r ? { rev: r.rev, rid: r.rid, at: r.at, size: r.size, sha256: r.sha256, device_name: r.device_name, meta: JSON.parse(r.meta) } : null;
  },
  conflict(owner, wid, error = 'В облаке уже другая версия этого мира') {
    return werr(409, 'conflict', error, { head: this.headInfo(owner, wid) });
  },
  // last_active — не чаще раза в сутки; пометку «потерял ник» снимает поздний удачный запрос (задержка KV, 4.3)
  touchOwner(owner, nick) {
    const now = Date.now();
    const r = this.rows('SELECT last_active, orphaned_at, nick FROM w_owners WHERE owner = ?', owner)[0];
    if (!r) { this.sql.exec('INSERT INTO w_owners (owner, nick, last_active, orphaned_at) VALUES (?, ?, ?, NULL)', owner, nick, now); return; }
    const unorphan = r.orphaned_at != null && now - r.orphaned_at > 10 * MIN;
    if (unorphan || now - r.last_active > DAY || r.nick !== nick) {
      this.sql.exec('UPDATE w_owners SET last_active = ?, nick = ?, orphaned_at = ? WHERE owner = ?',
        now, nick, unorphan ? null : r.orphaned_at, owner);
    }
  },
  async abortSessions(list) {
    for (const s of list) {
      if (!s.upload_id) continue;   // ещё создаётся: тот запрос сам отменит загрузку, увидев aborted
      try {
        await this.env.WORLDS.resumeMultipartUpload(s.obj, s.upload_id).abort();
        this.sql.exec("DELETE FROM w_sessions WHERE sid = ? AND state = 'aborted'", s.sid);
      } catch (err) { console.error('world abort', err); }   // место держится, будильник повторит
    }
    if (list.length) await this.ensureAlarm();
  },
  markAborted(list) {
    for (const s of list) this.sql.exec("UPDATE w_sessions SET state = 'aborted', touched = ? WHERE sid = ?", Date.now(), s.sid);
  },

  /* -------- POST /api/worlds */
  async worldsCall(owner, nick, body, ip) {
    if (body.v !== undefined && body.v !== 1) return bad('Неизвестная версия протокола облака');
    const a = String(body.action || '');
    if (a === 'list') return this.wList(owner, nick, body, (await sha256hex('tag:' + owner)).slice(0, 8));
    if (a === 'begin') return this.wBegin(owner, nick, body, ip, (await sha256hex(owner)).slice(0, 16));
    if (a === 'commit') return this.wCommit(owner, nick, body);
    if (a === 'abort') return this.wAbort(owner, body);
    if (a === 'lease') return this.wLease(owner, body);
    if (a === 'release') return this.wRelease(owner, body);
    if (a === 'restore') return this.wRestore(owner, nick, body);
    if (a === 'delete') return this.wDelete(owner, body);
    return bad('Неизвестное действие');
  },

  wList(owner, nick, b, ownerTag) {
    const now = Date.now();
    const device = DEV_RE.test(String(b.device || '')) ? String(b.device) : '';
    const bases = b.bases && typeof b.bases === 'object' && !Array.isArray(b.bases) ? b.bases : {};
    const worlds = [];
    const seen = new Set();
    for (const w of this.rows('SELECT * FROM w_worlds WHERE owner = ? ORDER BY created', owner)) {
      seen.add(w.wid);
      const head = this.headInfo(owner, w.wid);
      const p = this.rows('SELECT rid, at, size, device_name FROM w_vers WHERE owner = ? AND wid = ? AND keep_until IS NOT NULL ORDER BY at DESC LIMIT 1', owner, w.wid)[0];
      let relation = null;
      if (Object.prototype.hasOwnProperty.call(bases, w.wid)) {
        const base = String(bases[w.wid] || '');
        if (base === w.head_rid) relation = 'same';
        else if (RID_RE.test(base) && this.rows('SELECT 1 FROM w_hist WHERE owner = ? AND wid = ? AND rid = ? AND rev < ?', owner, w.wid, base, w.head_rev).length) relation = 'behind';
        else relation = 'unknown';
      }
      const l = this.rows('SELECT device_name, until FROM w_leases WHERE owner = ? AND wid = ? AND device != ? AND until > ? ORDER BY until DESC LIMIT 1', owner, w.wid, device, now)[0];
      const u = this.rows(`SELECT device_name, touched FROM w_sessions WHERE owner = ? AND wid = ? AND device != ? AND state IN ${ACTIVE} ORDER BY touched DESC LIMIT 1`, owner, w.wid, device)[0];
      worlds.push({
        wid: w.wid, title: w.title, head, prev: p ? { rid: p.rid, at: p.at, size: p.size, device_name: p.device_name } : null,
        relation, lease: l ? { device_name: l.device_name, until: l.until } : null,
        upload: u ? { device_name: u.device_name, touched: u.touched } : null,
      });
    }
    const goneList = Object.keys(bases).slice(0, 50).filter((wid) => WID_RE.test(wid) && !seen.has(wid));
    this.touchOwner(owner, nick);
    return wj({
      ok: true, worlds, gone: goneList, owner_tag: ownerTag,
      usage: { used: this.ownerUsed(owner), quota: W.QUOTA, worlds: worlds.length, max_worlds: W.WORLDS },
      limits: { max_world: W.MAX_WORLD, part_size: W.PART },
    });
  },

  async wBegin(owner, nick, b, ip, h) {
    const wid = String(b.wid || ''), base = String(b.base || ''), sha = String(b.sha256 || '');
    const device = String(b.device || ''), size = Number(b.size);
    if (!WID_RE.test(wid) || (base && !RID_RE.test(base)) || !SHA_RE.test(sha) || !DEV_RE.test(device)) return bad();
    if (!Number.isInteger(size) || size < 1) return bad('Неверный размер');
    if (size > W.MAX_WORLD) return werr(413, 'too_big', 'Мир слишком большой для облака', { max_world: W.MAX_WORLD });
    const now = Date.now();
    if (this.opsFull()) return full();
    const world = this.rows('SELECT * FROM w_worlds WHERE owner = ? AND wid = ?', owner, wid)[0];
    const knownOwner = this.rows('SELECT 1 FROM w_owners WHERE owner = ?', owner).length > 0;
    if (!world) {
      if (!b.new) return gone();
      if (base) return bad();
      if (this.rows('SELECT COUNT(*) AS n FROM w_worlds WHERE owner = ?', owner)[0].n >= W.WORLDS) {
        return werr(507, 'quota', `В облаке уже ${W.WORLDS} миров — удалите из облака ненужный`, { reason: 'worlds', max_worlds: W.WORLDS, used: this.ownerUsed(owner), quota: W.QUOTA });
      }
      if (!knownOwner && this.wCount('newowner:' + ip) >= W.NEW_OWNER_DAY) return limited('С вашего адреса сегодня уже включали облако для нескольких игроков. Попробуйте завтра');
    }
    if (base !== (world ? world.head_rid : '')) return this.conflict(owner, wid);
    // одна живая загрузка на мир: свою прежнюю (повтор после потерянного ответа) и заброшенную чужую отменяем
    const cancel = [];
    for (const s of this.rows(`SELECT * FROM w_sessions WHERE owner = ? AND wid = ? AND state IN ${ACTIVE}`, owner, wid)) {
      if (s.state === 'committing') {
        return s.device === device ? werr(409, 'in_progress', 'Отправка этого мира уже завершается') : werr(409, 'busy', 'Другой ПК отправляет этот мир', { device_name: s.device_name });
      }
      if (s.device === device || now - s.touched > W.BUSY) cancel.push(s);
      else return werr(409, 'busy', 'Другой ПК отправляет этот мир', { device_name: s.device_name, touched: s.touched });
    }
    const ex = cancel.map((s) => s.sid);
    const liveOwner = this.rows(`SELECT sid FROM w_sessions WHERE owner = ? AND state IN ${ACTIVE}`, owner).filter((r) => !ex.includes(r.sid)).length;
    if (liveOwner >= W.LIVE_PER_OWNER) return limited('Сейчас отправляется слишком много миров. Подождите');
    if (this.wCount(`begin:${owner}:${today()}`) >= W.BEGIN_DAY) return limited('Сегодня миры отправлялись слишком часто. Попробуйте завтра');
    const room = this.makeRoom(owner, size, ex);
    if (room) return room;

    // запись одним куском: мир (если новый), сессия «creating» (место зарезервировано), отмена прежних
    const meta = cleanMeta(b.meta);
    if (!world) this.sql.exec('INSERT INTO w_worlds (owner, wid, title, head_rev, head_rid, created, updated) VALUES (?, ?, ?, 0, \'\', ?, ?)', owner, wid, meta.title || 'Мир', now, now);
    const sid = rhex(32), rid = rhex(16), obj = `w/${h}/${wid}/${rid}.zip`, parts = Math.ceil(size / W.PART);
    this.sql.exec(
      'INSERT INTO w_sessions (sid, owner, wid, base_rid, rid, obj, upload_id, size, part_size, parts, sha256, meta, device, device_name, state, rev, created, touched, exp, puts) VALUES (?, ?, ?, ?, ?, ?, \'\', ?, ?, ?, ?, ?, ?, ?, \'creating\', NULL, ?, ?, ?, 0)',
      sid, owner, wid, base, rid, obj, size, W.PART, parts, sha, JSON.stringify(meta), device, devName(b.device_name), now, now, now + W.SESSION_IDLE);
    this.markAborted(cancel);
    if (!knownOwner) this.wBump('newowner:' + ip, DAY);
    this.touchOwner(owner, nick);
    this.opsAdd();

    let up;
    try {
      up = await this.env.WORLDS.createMultipartUpload(obj, { customMetadata: { wid, rid } });
    } catch (err) {
      console.error('world begin', err);
      this.sql.exec('DELETE FROM w_sessions WHERE sid = ?', sid);
      await this.abortSessions(cancel);
      return werr(500, 'server', 'Хранилище не ответило, попробуйте ещё раз');
    }
    const cur = this.rows('SELECT state FROM w_sessions WHERE sid = ?', sid)[0];
    if (!cur || cur.state !== 'creating') {   // пока ждали R2, загрузку отменил повторный begin
      try { await up.abort(); } catch (err) { console.error('world begin abort', err); }
      this.sql.exec('DELETE FROM w_sessions WHERE sid = ?', sid);
      return noSession();
    }
    this.sql.exec("UPDATE w_sessions SET upload_id = ?, state = 'open' WHERE sid = ?", up.uploadId, sid);
    this.wBump(`begin:${owner}:${today()}`, DAY);
    await this.abortSessions(cancel);
    await this.ensureAlarm();
    return wj({ ok: true, sid, wid, rid, part_size: W.PART, parts });
  },

  // PUT /api/worlds/part: проверка до R2. Каждая попытка — платная операция R2, поэтому их число ограничено.
  async worldPart(m) {
    const ip = String(m.ip || ''), sid = String(m.sid || ''), n = Number(m.n), len = Number(m.len);
    if (this.reached('wpartbad:' + ip, W.PART_BAD)) return limited('Слишком много неверных запросов. Подождите 10 минут');
    const s = SID_RE.test(sid) ? this.rows("SELECT * FROM w_sessions WHERE sid = ? AND state = 'open'", sid)[0] : null;
    if (!s) { this.hit('wpartbad:' + ip, W.PART_BAD); return noSession(); }
    if (!Number.isInteger(n) || n < 1 || n > s.parts) return werr(400, 'part', 'Номер части вне диапазона');
    const expected = n < s.parts ? s.part_size : s.size - s.part_size * (s.parts - 1);
    if (len !== expected) return werr(400, 'part', 'Неверная длина части', { expected });
    const now = Date.now();
    if (s.puts + 1 > 2 * s.parts + 5) {
      this.markAborted([s]);
      await this.ensureAlarm();
      return noSession();
    }
    if (this.opsFull()) return full();
    this.opsAdd();
    this.sql.exec('UPDATE w_sessions SET puts = puts + 1, touched = ?, exp = ? WHERE sid = ?',
      now, Math.min(now + W.SESSION_IDLE, s.created + W.SESSION_MAX), sid);
    return wj({ ok: true, obj: s.obj, upload_id: s.upload_id });
  },

  async wCommit(owner, nick, b) {
    const sid = String(b.sid || '');
    const s = SID_RE.test(sid) ? this.rows('SELECT * FROM w_sessions WHERE sid = ? AND owner = ?', sid, owner)[0] : null;
    if (!s) return noSession();
    if (s.state === 'committed') return wj({ ok: true, rev: s.rev, rid: s.rid, at: s.touched });
    if (s.state === 'discarded') {
      return this.rows('SELECT 1 FROM w_worlds WHERE owner = ? AND wid = ?', owner, s.wid).length ? this.conflict(owner, s.wid) : gone();
    }
    if (s.state === 'committing' && this.committing.has(sid)) return werr(409, 'in_progress', 'Отправка уже завершается, подождите');
    if (s.state !== 'open' && s.state !== 'committing') return noSession();
    const parts = Array.isArray(b.parts) ? b.parts : [];
    const nums = new Set(parts.map((p) => p && p.n));
    const okParts = parts.length === s.parts && nums.size === s.parts
      && parts.every((p) => p && Number.isInteger(p.n) && p.n >= 1 && p.n <= s.parts && typeof p.etag === 'string' && p.etag.length > 0 && p.etag.length <= 100);
    if (!okParts) return werr(400, 'parts', 'Список частей неполный');
    const world = this.rows('SELECT head_rid FROM w_worlds WHERE owner = ? AND wid = ?', owner, s.wid)[0];
    if (!world || world.head_rid !== s.base_rid) {   // до сборки файла: мир удалили или другой ПК успел раньше
      if (s.state === 'open') {
        this.sql.exec("UPDATE w_sessions SET state = ?, touched = ? WHERE sid = ?", world ? 'discarded' : 'aborted', Date.now(), sid);
        if (world) {
          try { await this.env.WORLDS.resumeMultipartUpload(s.obj, s.upload_id).abort(); } catch (err) { console.error('world commit abort', err); }
        } else {
          await this.abortSessions([s]);
        }
        return world ? this.conflict(owner, s.wid) : gone();
      }
    }
    const stale = s.state === 'committing';   // осталась от прошлого запуска объекта — доводим
    if (!stale && this.opsFull()) return full();
    this.sql.exec("UPDATE w_sessions SET state = 'committing', touched = ? WHERE sid = ?", Date.now(), sid);
    this.committing.add(sid);
    let done = false;
    try {
      if (stale) {
        const o = await this.env.WORLDS.head(s.obj);
        if (o) done = o.size === s.size ? true : 'size';
      }
      if (!done) {
        this.opsAdd();
        const o = await this.env.WORLDS.resumeMultipartUpload(s.obj, s.upload_id)
          .complete(parts.map((p) => ({ partNumber: p.n, etag: p.etag })));
        done = o && o.size === s.size ? true : 'size';
      }
    } catch (err) {
      // complete мог пройти, а потерялся только ответ: смотрим, есть ли файл
      try { const o = await this.env.WORLDS.head(s.obj); if (o) done = o.size === s.size ? true : 'size'; } catch (e2) { console.error('world commit head', e2); }
      if (!done) {
        console.error('world commit', err);
        this.committing.delete(sid);
        this.sql.exec("UPDATE w_sessions SET state = 'open' WHERE sid = ? AND state = 'committing'", sid);
        return werr(400, 'parts', 'Хранилище не собрало файл из частей — отправьте части ещё раз');
      }
    }
    this.committing.delete(sid);
    if (done === 'size') {
      this.sql.exec('DELETE FROM w_sessions WHERE sid = ?', sid);
      this.trashIfFree(s.obj);
      await this.ensureAlarm();
      return noSession();
    }
    const res = this.finishCommit(owner, nick, sid);
    await this.ensureAlarm();
    return res;
  },

  // После сборки файла — одним куском: ещё раз «база == текущая версия», потом запись новой версии.
  finishCommit(owner, nick, sid) {
    const s = this.rows('SELECT * FROM w_sessions WHERE sid = ?', sid)[0];
    const now = Date.now();
    const world = this.rows('SELECT * FROM w_worlds WHERE owner = ? AND wid = ?', owner, s.wid)[0];
    const discard = () => {
      this.sql.exec("UPDATE w_sessions SET state = 'discarded', touched = ?, exp = ? WHERE sid = ?", now, s.created + W.SESSION_MAX, sid);
      this.trashIfFree(s.obj);
    };
    if (!world) { discard(); return gone(); }
    if (world.head_rid !== s.base_rid) { discard(); return this.conflict(owner, s.wid, 'Другой ПК отправил этот мир раньше'); }
    const rev = world.head_rev + 1;
    const meta = JSON.parse(s.meta);
    for (const r of this.prevRows(owner).filter((r) => r.wid === s.wid)) this.dropVersion(r);
    this.sql.exec('UPDATE w_vers SET keep_until = ? WHERE owner = ? AND wid = ? AND keep_until IS NULL', now + W.PREV_KEEP, owner, s.wid);
    this.sql.exec('INSERT INTO w_vers (owner, wid, rid, rev, obj, size, sha256, at, device, device_name, meta, keep_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)',
      owner, s.wid, s.rid, rev, s.obj, s.size, s.sha256, now, s.device, s.device_name, s.meta);
    this.addHist(owner, s.wid, rev, s.rid, now, s.device_name);
    this.sql.exec('UPDATE w_worlds SET head_rev = ?, head_rid = ?, updated = ?, title = ? WHERE owner = ? AND wid = ?',
      rev, s.rid, now, meta.title || world.title, owner, s.wid);
    this.sql.exec("UPDATE w_sessions SET state = 'committed', rev = ?, touched = ?, exp = ? WHERE sid = ?", rev, now, s.created + W.SESSION_MAX, sid);
    this.touchOwner(owner, nick);
    return wj({ ok: true, rev, rid: s.rid, at: now });
  },
  addHist(owner, wid, rev, rid, at, deviceName) {
    this.sql.exec('INSERT OR REPLACE INTO w_hist (owner, wid, rev, rid, at, device_name) VALUES (?, ?, ?, ?, ?, ?)', owner, wid, rev, rid, at, deviceName);
    this.sql.exec('DELETE FROM w_hist WHERE owner = ? AND wid = ? AND rev <= ?', owner, wid, rev - W.HISTORY);
  },

  async wAbort(owner, b) {
    const sid = String(b.sid || ''), wid = String(b.wid || '');
    let list;
    if (SID_RE.test(sid)) list = this.rows("SELECT * FROM w_sessions WHERE sid = ? AND owner = ? AND state IN ('creating', 'open', 'aborted')", sid, owner);
    else if (WID_RE.test(wid)) list = this.rows("SELECT * FROM w_sessions WHERE owner = ? AND wid = ? AND state IN ('creating', 'open', 'aborted')", owner, wid);
    else return bad();
    this.markAborted(list);
    await this.abortSessions(list);
    return wj({ ok: true });
  },

  wLease(owner, b) {
    const device = String(b.device || '');
    const wids = (Array.isArray(b.wids) ? b.wids : []).slice(0, 20).map(String).filter((w) => WID_RE.test(w));
    if (!DEV_RE.test(device)) return bad();
    const now = Date.now(), name = devName(b.device_name);
    const leases = {};
    for (const wid of wids) {
      if (!this.rows('SELECT 1 FROM w_worlds WHERE owner = ? AND wid = ?', owner, wid).length) continue;
      this.sql.exec('INSERT OR REPLACE INTO w_leases (owner, wid, device, device_name, until) VALUES (?, ?, ?, ?, ?)', owner, wid, device, name, now + W.LEASE);
      const l = this.rows('SELECT device_name, until FROM w_leases WHERE owner = ? AND wid = ? AND device != ? AND until > ? ORDER BY until DESC LIMIT 1', owner, wid, device, now)[0];
      if (l) leases[wid] = { device_name: l.device_name, until: l.until };
    }
    this.sql.exec('DELETE FROM w_leases WHERE owner = ? AND until <= ?', owner, now);
    return wj({ ok: true, leases });
  },

  wRelease(owner, b) {
    const device = String(b.device || '');
    if (!DEV_RE.test(device)) return bad();
    for (const wid of (Array.isArray(b.wids) ? b.wids : []).slice(0, 20).map(String)) {
      this.sql.exec('DELETE FROM w_leases WHERE owner = ? AND wid = ? AND device = ?', owner, wid, device);
    }
    return wj({ ok: true });
  },

  // Вернуть прошлую версию: она становится новой текущей (номера только растут — другие ПК увидят behind).
  // Файл переходит к новой строке, у файла всегда одна строка w_vers.
  wRestore(owner, nick, b) {
    const wid = String(b.wid || ''), rid = String(b.rid || ''), ifHead = String(b.if_head || '');
    if (!WID_RE.test(wid) || !RID_RE.test(rid)) return bad();
    const world = this.rows('SELECT * FROM w_worlds WHERE owner = ? AND wid = ?', owner, wid)[0];
    if (!world) return gone();
    if (ifHead !== world.head_rid) return this.conflict(owner, wid);
    const p = this.rows('SELECT * FROM w_vers WHERE owner = ? AND wid = ? AND rid = ? AND keep_until IS NOT NULL', owner, wid, rid)[0];
    if (!p) return this.conflict(owner, wid, 'Прошлой версии уже нет');
    const now = Date.now(), rev = world.head_rev + 1, nrid = rhex(16);
    this.sql.exec('DELETE FROM w_vers WHERE owner = ? AND wid = ? AND rid = ?', owner, wid, rid);
    for (const r of this.prevRows(owner).filter((r) => r.wid === wid)) this.dropVersion(r);
    this.sql.exec('UPDATE w_vers SET keep_until = ? WHERE owner = ? AND wid = ? AND keep_until IS NULL', now + W.PREV_KEEP, owner, wid);
    this.sql.exec('INSERT INTO w_vers (owner, wid, rid, rev, obj, size, sha256, at, device, device_name, meta, keep_until) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)',
      owner, wid, nrid, rev, p.obj, p.size, p.sha256, now, p.device, p.device_name, p.meta);
    this.addHist(owner, wid, rev, nrid, now, p.device_name);
    this.sql.exec('UPDATE w_worlds SET head_rev = ?, head_rid = ?, updated = ? WHERE owner = ? AND wid = ?', rev, nrid, now, owner, wid);
    this.touchOwner(owner, nick);
    return wj({ ok: true, rev, rid: nrid, at: now });
  },

  async wDelete(owner, b) {
    const wid = String(b.wid || '');
    if (!WID_RE.test(wid)) return bad();
    if (!this.rows('SELECT 1 FROM w_worlds WHERE owner = ? AND wid = ?', owner, wid).length) return wj({ ok: true });
    const list = this.dropWorld(owner, wid);
    await this.abortSessions(list);
    await this.ensureAlarm();
    return wj({ ok: true });
  },
  // Убрать мир целиком (синхронно): файлы — в корзину, живые загрузки — отменить (возвращает их для abort в R2).
  dropWorld(owner, wid) {
    const list = this.rows("SELECT * FROM w_sessions WHERE owner = ? AND wid = ? AND state IN ('creating', 'open', 'aborted')", owner, wid);
    this.markAborted(list);
    const objs = this.rows('SELECT obj FROM w_vers WHERE owner = ? AND wid = ?', owner, wid).map((r) => r.obj);
    for (const t of ['w_vers', 'w_hist', 'w_leases', 'w_worlds']) this.sql.exec(`DELETE FROM ${t} WHERE owner = ? AND wid = ?`, owner, wid);
    for (const o of objs) this.trashIfFree(o);
    return list;
  },

  /* -------- скачивание: права и лимит, сами байты отдаёт воркер */
  worldFile(owner, b) {
    const wid = String(b.wid || ''), rid = String(b.rid || '');
    if (!WID_RE.test(wid) || !RID_RE.test(rid)) return bad();
    if (!this.rows('SELECT 1 FROM w_worlds WHERE owner = ? AND wid = ?', owner, wid).length) return gone();
    const v = this.rows('SELECT obj, size, sha256 FROM w_vers WHERE owner = ? AND wid = ? AND rid = ?', owner, wid, rid)[0];
    if (!v) return this.conflict(owner, wid, 'Этой версии уже нет — обновите список');
    if (this.wBump(`file:${owner}:${today()}`, DAY) > W.FILE_DAY) return limited('Сегодня миры скачивались слишком часто. Попробуйте завтра');
    return wj({ ok: true, obj: v.obj, size: v.size, sha256: v.sha256 });
  },

  // Владелец потерял ник (захват): миры — к удалению через 7 дней. Только для o: (у e: бывает несколько ников).
  worldsOrphan(owner) {
    if (!/^o:[0-9a-f]{32}$/.test(String(owner || ''))) return wj({ ok: true, skipped: true });
    this.sql.exec('UPDATE w_owners SET orphaned_at = ? WHERE owner = ?', Date.now(), owner);
    return this.ensureAlarm().then(() => wj({ ok: true }));
  },

  worldsStats() {
    const one = (q) => this.rows(q)[0].n;
    return {
      owners: one('SELECT COUNT(*) AS n FROM w_owners'), worlds: one('SELECT COUNT(*) AS n FROM w_worlds WHERE head_rev > 0'),
      bytes: this.globalUsed(), sessions: one(`SELECT COUNT(*) AS n FROM w_sessions WHERE state IN ${ACTIVE}`),
      trash: one('SELECT COUNT(*) AS n FROM w_trash'), ops_month: this.wCount('ops:' + month()),
    };
  },

  /* -------- уборка (будильник объекта, раздел 6.6) */
  async worldsAlarm() {
    if (!this.env.WORLDS) return;
    const now = Date.now();
    let budget = 900;   // обращений к R2 за запуск (на бесплатном тарифе до 1000)
    const r2 = this.env.WORLDS;

    // загрузки: истёкшие, отменённые, «creating» после перезапуска объекта, застрявшие в committing
    this.sql.exec("DELETE FROM w_sessions WHERE state IN ('committed', 'discarded') AND exp <= ?", now);
    this.sql.exec("DELETE FROM w_sessions WHERE state IN ('creating', 'aborted') AND upload_id = '' AND touched <= ?", now - 10 * MIN);
    this.sql.exec("UPDATE w_sessions SET state = 'aborted', touched = ? WHERE state = 'open' AND exp <= ?", now, now);
    for (const s of this.rows("SELECT * FROM w_sessions WHERE state = 'committing' AND touched <= ?", now - HOUR)) {
      if (this.committing.has(s.sid)) continue;
      if (budget-- > 0) { try { await r2.resumeMultipartUpload(s.obj, s.upload_id).abort(); } catch { /* уже собран или отменён */ } }
      this.sql.exec("UPDATE w_sessions SET state = 'discarded', touched = ?, exp = ? WHERE sid = ?", now, now + DAY, s.sid);
      this.sql.exec('INSERT OR IGNORE INTO w_trash (obj, at) VALUES (?, ?)', s.obj, now);   // complete мог успеть
    }
    for (const s of this.rows("SELECT * FROM w_sessions WHERE state = 'aborted' AND upload_id != '' LIMIT 200")) {
      if (budget-- <= 0) break;
      try {
        await r2.resumeMultipartUpload(s.obj, s.upload_id).abort();
        this.sql.exec("DELETE FROM w_sessions WHERE sid = ? AND state = 'aborted'", s.sid);
      } catch (err) { console.error('world alarm abort', err); }
    }
    // прошлые версии, у которых вышел срок
    for (const r of this.rows('SELECT * FROM w_vers WHERE keep_until IS NOT NULL AND keep_until < ?', now)) this.dropVersion(r);
    // владельцы: потеряли ник 7+ дней назад или 90 дней не пользовались облаком
    for (const o of this.rows('SELECT owner FROM w_owners WHERE (orphaned_at IS NOT NULL AND orphaned_at < ?) OR last_active < ?', now - W.ORPHAN, now - W.INACTIVE)) {
      for (const w of this.rows('SELECT wid FROM w_worlds WHERE owner = ?', o.owner)) this.dropWorld(o.owner, w.wid);
      this.sql.exec('DELETE FROM w_owners WHERE owner = ?', o.owner);
    }
    // пустые миры (первая отправка так и не завершилась)
    this.sql.exec(`DELETE FROM w_worlds WHERE head_rev = 0 AND created < ? AND NOT EXISTS (SELECT 1 FROM w_sessions s WHERE s.owner = w_worlds.owner AND s.wid = w_worlds.wid AND s.state IN ${LIVE})`, now - HOUR);
    this.sql.exec('DELETE FROM w_leases WHERE until <= ?', now);
    // сверка бакета раз в сутки: объекты старше суток, на которые нет ни одной ссылки, — сироты
    budget = await this.reconcile(now, budget);
    // корзина → R2 пачками
    while (budget-- > 0) {
      const batch = this.rows('SELECT obj FROM w_trash ORDER BY at LIMIT 500').map((r) => r.obj);
      if (!batch.length) break;
      try { await r2.delete(batch); } catch (err) { console.error('world alarm delete', err); break; }
      for (const o of batch) this.sql.exec('DELETE FROM w_trash WHERE obj = ?', o);
    }
    // следующий запуск: через час, если есть работа; раз в сутки, если миры просто лежат
    const busy = this.rows(`SELECT 1 FROM w_sessions LIMIT 1`).length || this.rows('SELECT 1 FROM w_trash LIMIT 1').length
      || this.rows('SELECT 1 FROM w_vers WHERE keep_until IS NOT NULL LIMIT 1').length;
    const any = busy || this.rows('SELECT 1 FROM w_worlds LIMIT 1').length || this.rows('SELECT 1 FROM w_owners LIMIT 1').length;
    if (any && this.storage && typeof this.storage.setAlarm === 'function') await this.storage.setAlarm(now + (busy ? W.ALARM : DAY));
  },

  async reconcile(now, budget) {
    const kv = (k) => { const r = this.rows('SELECT v FROM w_kv WHERE k = ?', k)[0]; return r ? r.v : ''; };
    const setKv = (k, v) => this.sql.exec('INSERT OR REPLACE INTO w_kv (k, v) VALUES (?, ?)', k, v);
    let cursor = kv('rec_cursor');
    if (!cursor && Number(kv('rec_next') || 0) > now) return budget;
    for (let page = 0; page < 20 && budget > 0; page++) {
      budget--;
      this.opsAdd();
      let res;
      try { res = await this.env.WORLDS.list({ prefix: 'w/', limit: 1000, ...(cursor ? { cursor } : {}) }); } catch (err) { console.error('world reconcile', err); return budget; }
      for (const o of res.objects || []) {
        const up = o.uploaded instanceof Date ? o.uploaded.getTime() : Number(o.uploaded || 0);
        if (now - up < DAY) continue;
        const used = this.rows('SELECT 1 FROM w_vers WHERE obj = ?', o.key).length
          || this.rows("SELECT 1 FROM w_sessions WHERE obj = ? AND state NOT IN ('discarded')", o.key).length
          || this.rows('SELECT 1 FROM w_trash WHERE obj = ?', o.key).length;
        if (!used) this.sql.exec('INSERT OR IGNORE INTO w_trash (obj, at) VALUES (?, ?)', o.key, now);
      }
      cursor = res.truncated ? res.cursor || '' : '';
      if (!cursor) break;
    }
    setKv('rec_cursor', cursor);
    if (!cursor) setKv('rec_next', String(now + DAY));
    return budget;
  },
};
