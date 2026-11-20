// Тесты облака миров (server/worlds.js + server/worker.js): поддельный R2 в памяти, который ведёт себя как
// настоящий — части одного размера кроме последней, etag = md5, complete проверяет etag. Запуск: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { createHash } from 'node:crypto';
import worker, { Social } from '../server/worker.js';
import { W } from '../server/worlds.js';

// маленькие пределы, чтобы не гонять мегабайты
Object.assign(W, { PART: 1024, MAX_WORLD: 8 * 1024, QUOTA: 20 * 1024, GLOBAL: 60 * 1024 });

// часы: тесты двигают время вперёд
const realNow = Date.now;
let shift = 0;
Date.now = () => realNow() + shift;
const later = (ms) => { shift += ms; };
const MIN = 60000, HOUR = 60 * MIN, DAY = 24 * HOUR;

const md5 = (b) => createHash('md5').update(b).digest('hex');
const sha = (b) => createHash('sha256').update(b).digest('hex');
const hex = (n, c = '0') => c.repeat(n);
const bytes = (n, seed = 1) => { const b = new Uint8Array(n); for (let i = 0; i < n; i++) b[i] = (i * 31 + seed) & 255; return b; };

function fakeR2() {
  const objects = new Map(), uploads = new Map();
  let seq = 0;
  const r2 = { objects, uploads, ops: { A: 0, B: 0 }, hooks: {} };
  const read = async (body) => (body instanceof Uint8Array ? body : new Uint8Array(await new Response(body).arrayBuffer()));
  const handle = (key, uploadId) => ({
    key, uploadId,
    async uploadPart(n, body) {
      r2.ops.A++;
      const u = uploads.get(uploadId);
      if (!u || u.key !== key) throw new Error('NoSuchUpload');
      const b = await read(body);
      const etag = md5(b);
      u.parts.set(n, { b, etag });
      return { partNumber: n, etag };
    },
    async abort() { if (r2.hooks.failAbort) throw new Error('abort failed'); uploads.delete(uploadId); },
    async complete(parts) {
      r2.ops.A++;
      if (r2.hooks.beforeComplete) await r2.hooks.beforeComplete();
      const u = uploads.get(uploadId);
      if (!u) throw new Error('NoSuchUpload');
      const sorted = [...parts].sort((a, b) => a.partNumber - b.partNumber);
      const chunks = sorted.map((p, i) => {
        const got = u.parts.get(p.partNumber);
        if (!got || got.etag !== p.etag) throw new Error('InvalidPart');
        if (i < sorted.length - 1 && got.b.length !== u.parts.get(sorted[0].partNumber).b.length) throw new Error('EntityTooSmall');
        return got.b;
      });
      const all = new Uint8Array(chunks.reduce((a, c) => a + c.length, 0));
      let off = 0;
      for (const c of chunks) { all.set(c, off); off += c.length; }
      objects.set(key, { b: all, uploaded: new Date(Date.now()) });
      uploads.delete(uploadId);
      if (r2.hooks.throwAfterComplete) { r2.hooks.throwAfterComplete = false; throw new Error('network'); }
      return { key, size: all.length };
    },
  });
  Object.assign(r2, {
    async createMultipartUpload(key) { r2.ops.A++; const id = 'u' + (++seq); uploads.set(id, { key, parts: new Map() }); return handle(key, id); },
    resumeMultipartUpload: (key, id) => handle(key, id),
    async head(key) { r2.ops.B++; const o = objects.get(key); return o ? { key, size: o.b.length, uploaded: o.uploaded } : null; },
    async get(key, opts) {
      r2.ops.B++;
      const o = objects.get(key);
      if (!o) return null;
      const b = o.b.subarray((opts && opts.range && opts.range.offset) || 0);
      return { body: new Blob([b]).stream(), size: o.b.length };
    },
    async delete(keys) { for (const k of [].concat(keys)) objects.delete(k); },
    async list({ prefix = '', cursor, limit = 1000 } = {}) {
      r2.ops.A++;
      const all = [...objects.keys()].filter((k) => k.startsWith(prefix)).sort();
      const start = cursor ? Number(cursor) : 0, next = start + limit;
      return {
        objects: all.slice(start, next).map((k) => ({ key: k, size: objects.get(k).b.length, uploaded: objects.get(k).uploaded })),
        truncated: next < all.length, cursor: next < all.length ? String(next) : undefined,
      };
    },
  });
  return r2;
}

function fakeKV() {
  const m = new Map();
  return {
    m,
    async get(k, type) { await new Promise((r) => setImmediate(r)); if (!m.has(k)) return null; const v = m.get(k); return type === 'json' ? JSON.parse(v) : v; },
    async put(k, v) { m.set(k, String(v)); },
    async delete(k) { m.delete(k); },
  };
}

function fakeSql() {
  const db = new DatabaseSync(':memory:');
  return {
    exec(query, ...args) {
      const st = db.prepare(query);
      const rows = /^\s*SELECT/i.test(query) ? st.all(...args) : (st.run(...args), []);
      return { toArray: () => rows.map((r) => ({ ...r })) };
    },
  };
}

// withR2 = false — сервер без привязки WORLDS
function env(withR2 = true) {
  const e = { SKINS: fakeKV() };
  if (withR2) e.WORLDS = fakeR2();
  const storage = { sql: fakeSql(), alarmAt: null, async getAlarm() { return this.alarmAt; }, async setAlarm(t) { this.alarmAt = t; } };
  e.storage = storage;
  e.restart = () => {   // перезапуск объекта: память пропадает, SQLite остаётся
    e.obj = new Social({ storage }, e);
    e.SOCIAL = { idFromName: (n) => n, get: () => ({ fetch: (url, init) => e.obj.fetch(new Request(url, init)) }) };
  };
  e.restart();
  return e;
}

// Ely.by: serverId из 32 hex → аккаунт с таким UUID; 'noid…' → ответ без id
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  const sid = u.searchParams.get('serverId');
  const name = u.searchParams.get('username');
  if (sid.startsWith('noid')) return new Response(JSON.stringify({ name }), { status: 200 });
  return new Response(JSON.stringify({ name, id: sid.slice(0, 32) }), { status: 200 });
};

async function post(e, path, body, ip = '1.1.1.1') {
  const res = await worker.fetch(new Request('https://x.test' + path, {
    method: 'POST', headers: { 'content-type': 'application/json', 'CF-Connecting-IP': ip }, body: JSON.stringify(body),
  }), e);
  const ct = res.headers.get('content-type') || '';
  return ct.includes('json') ? { status: res.status, data: await res.json() } : { status: res.status, res, data: {} };
}
async function putPart(e, sid, n, b, { md5sum = md5(b), len = b.length, ip = '1.1.1.1' } = {}) {
  const res = await worker.fetch(new Request('https://x.test/api/worlds/part', {
    method: 'PUT', body: b,
    headers: { 'content-length': String(len), 'x-vmc-upload': sid, 'x-vmc-part': String(n), 'x-vmc-md5': md5sum, 'CF-Connecting-IP': ip },
  }), e);
  return { status: res.status, data: await res.json() };
}

const TA = 'a'.repeat(40), TB = 'b'.repeat(40), TC = 'c'.repeat(40);
const DEV1 = hex(32, '1'), DEV2 = hex(32, '2');
const WID = hex(24, 'a'), WID2 = hex(24, 'b');
const as = (name, token) => ({ name, token });
const reg = (e, name, token, extra = {}, ip) => post(e, '/api/register', { name, token, ...extra }, ip);
const w = (e, who, body, ip) => post(e, '/api/worlds', { ...who, v: 1, ...body }, ip);

async function begin(e, who, { wid = WID, base = '', data, device = DEV1, isNew = !base, meta = { title: 'Мой мир' }, ip } = {}) {
  return w(e, who, { action: 'begin', wid, base, size: data.length, sha256: sha(data), meta, device, device_name: 'ПК ' + device[0], new: isNew }, ip);
}
async function sendParts(e, s, data) {
  const parts = [];
  for (let n = 1; n <= s.parts; n++) {
    const r = await putPart(e, s.sid, n, data.subarray((n - 1) * s.part_size, n * s.part_size));
    assert.equal(r.status, 200, JSON.stringify(r.data));
    parts.push({ n, etag: r.data.etag });
  }
  return parts;
}
async function upload(e, who, opts) {
  const b = await begin(e, who, opts);
  if (b.status !== 200) return b;
  const parts = await sendParts(e, b.data, opts.data);
  return w(e, who, { action: 'commit', sid: b.data.sid, parts });
}
async function download(e, who, wid, rid, offset) {
  const r = await post(e, '/api/worlds/file', { ...who, wid, rid, ...(offset !== undefined ? { offset } : {}) });
  if (r.status !== 200) return r;
  return { status: 200, size: Number(r.res.headers.get('x-vmc-size')), sha: r.res.headers.get('x-vmc-sha256'), b: new Uint8Array(await r.res.arrayBuffer()) };
}
const list = (e, who, bases = {}, device = DEV1) => w(e, who, { action: 'list', device, bases });
const sqlRows = (e, q, ...a) => e.storage.sql.exec(q, ...a).toArray();

/* ---------------------------------------------------------------- включено / выключено */
test('без R2: облако отвечает disabled, остальной сервер работает', async () => {
  const e = env(false);
  assert.equal((await reg(e, 'Steve_1', TA)).status, 200);
  const st = await (await worker.fetch(new Request('https://x.test/api/status'), e)).json();
  assert.equal(st.worlds, null);
  const r = await list(e, as('Steve_1', TA));
  assert.equal(r.status, 503);
  assert.equal(r.data.code, 'disabled');
  assert.equal((await putPart(e, hex(32), 1, bytes(10))).data.code, 'disabled');
  assert.equal((await post(e, '/api/presence', { ...as('Steve_1', TA), status: null })).status, 200);
});

test('полный путь: begin → части → commit → list → file с докачкой', async () => {
  const e = env();
  const st = await (await worker.fetch(new Request('https://x.test/api/status'), e)).json();
  assert.deepEqual(st.worlds, { v: 1, max_world: W.MAX_WORLD, part_size: W.PART });
  await reg(e, 'Steve_1', TA);
  const data = bytes(2500);
  const c = await upload(e, as('Steve_1', TA), { data });
  assert.equal(c.status, 200, JSON.stringify(c.data));
  assert.equal(c.data.rev, 1);
  const l = await list(e, as('Steve_1', TA), { [WID]: c.data.rid });
  assert.equal(l.data.worlds.length, 1);
  assert.equal(l.data.worlds[0].relation, 'same');
  assert.equal(l.data.worlds[0].head.size, 2500);
  assert.equal(l.data.worlds[0].head.meta.title, 'Мой мир');
  assert.match(l.data.owner_tag, /^[0-9a-f]{8}$/);
  assert.equal(l.data.usage.used, 2500);
  const d = await download(e, as('Steve_1', TA), WID, c.data.rid);
  assert.equal(d.status, 200);
  assert.equal(sha(d.b), sha(data));
  assert.equal(d.sha, sha(data));
  const tail = await download(e, as('Steve_1', TA), WID, c.data.rid, 2000);
  assert.deepEqual([...tail.b], [...data.subarray(2000)]);
  // ключа ника в адресе и заголовках нет — ключ объекта случайный, без ника
  const key = [...e.WORLDS.objects.keys()][0];
  assert.match(key, /^w\/[0-9a-f]{16}\/[0-9a-f]{24}\/[0-9a-f]{16}\.zip$/);
  assert.ok(!key.toLowerCase().includes('steve'));
});

/* ---------------------------------------------------------------- права */
test('чужой ник не видит и не скачивает чужие миры', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  await reg(e, 'Alex_1', TB);
  const data = bytes(1500);
  const b = await begin(e, as('Steve_1', TA), { data });
  const parts = await sendParts(e, b.data, data);
  assert.equal((await w(e, as('Alex_1', TB), { action: 'commit', sid: b.data.sid, parts })).data.code, 'session');
  const c = await w(e, as('Steve_1', TA), { action: 'commit', sid: b.data.sid, parts });
  assert.equal(c.status, 200);
  const l = await list(e, as('Alex_1', TB), { [WID]: c.data.rid });
  assert.deepEqual(l.data.worlds, []);
  assert.deepEqual(l.data.gone, [WID]);
  assert.equal((await download(e, as('Alex_1', TB), WID, c.data.rid)).data.code, 'gone');
  assert.equal((await w(e, as('Alex_1', TB), { action: 'delete', wid: WID })).status, 200);
  assert.equal((await list(e, as('Steve_1', TA))).data.worlds.length, 1, 'чужой delete ничего не удалил');
  assert.equal((await w(e, as('Alex_1', TB), { action: 'begin', wid: WID, base: c.data.rid, size: 10, sha256: hex(64), device: DEV2 })).data.code, 'gone');
});

test('смена ника: миры остаются; занявший старый ник их не видит', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const c = await upload(e, as('Steve_1', TA), { data: bytes(1200) });
  const tag1 = (await list(e, as('Steve_1', TA))).data.owner_tag;
  const rn = await post(e, '/api/rename', { ...as('Steve_1', TA), new: 'Steve_2', newToken: TB });
  assert.equal(rn.status, 200);
  const l = await list(e, as('Steve_2', TB), { [WID]: c.data.rid });
  assert.equal(l.data.worlds.length, 1);
  assert.equal(l.data.owner_tag, tag1);
  await reg(e, 'Steve_1', TC);   // старый ник занял другой игрок
  const l2 = await list(e, as('Steve_1', TC), { [WID]: c.data.rid });
  assert.deepEqual(l2.data.worlds, []);
  assert.notEqual(l2.data.owner_tag, tag1);
});

test('смена пароля и сброс кодом сохраняют владельца', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  await upload(e, as('Steve_1', TA), { data: bytes(1200) });
  assert.equal((await reg(e, 'Steve_1', TB, { old: TA, replace: true })).status, 200);
  assert.equal((await list(e, as('Steve_1', TB))).data.worlds.length, 1);
  const code = 'ABCDEFGHJKLMNPQR';
  assert.equal((await post(e, '/api/recovery', { ...as('Steve_1', TB), code })).status, 200);
  assert.equal((await reg(e, 'Steve_1', TC, { recovery: code })).status, 200);
  assert.equal((await list(e, as('Steve_1', TC))).data.worlds.length, 1);
  const u = JSON.parse(e.SKINS.m.get('user:steve_1'));
  assert.match(u.owner, /^o:[0-9a-f]{32}$/);
});

test('старая запись без owner: владелец вычисляется, после перезаписи совпадает', async () => {
  const e = env();
  e.SKINS.m.set('user:old_1', JSON.stringify({ name: 'Old_1', hash: sha(TA), hashes: [sha(TA)], ely: false, created: 1700000000000 }));
  const tag = (await list(e, as('Old_1', TA))).data.owner_tag;
  await upload(e, as('Old_1', TA), { data: bytes(1100) });
  await reg(e, 'Old_1', TB, { old: TA });   // запись перезаписана — owner сохранён явно
  assert.ok(JSON.parse(e.SKINS.m.get('user:old_1')).owner);
  const l = await list(e, as('Old_1', TB));
  assert.equal(l.data.owner_tag, tag);
  assert.equal(l.data.worlds.length, 1);
});

/* ---------------------------------------------------------------- Ely.by */
const UUID1 = hex(32, 'e'), UUID2 = hex(32, 'f');

test('Ely.by: тот же UUID добавляет ключ (миры те же), другой UUID — захват', async () => {
  const e = env();
  await reg(e, 'Ely_1', TA, { ely: UUID1 });
  await upload(e, as('Ely_1', TA), { data: bytes(1100) });
  assert.equal((await reg(e, 'Ely_1', TB, { ely: UUID1 })).status, 200);
  assert.equal((await list(e, as('Ely_1', TB))).data.worlds.length, 1);
  assert.equal((await list(e, as('Ely_1', TA))).data.worlds.length, 1, 'первый ключ тоже действует');
  // имя на Ely.by сменило хозяина
  assert.equal((await reg(e, 'Ely_1', TC, { ely: UUID2 })).status, 200);
  assert.deepEqual((await list(e, as('Ely_1', TC))).data.worlds, []);
  assert.equal((await list(e, as('Ely_1', TA))).status, 403, 'ключи прежнего хозяина больше не действуют');
  assert.equal(sqlRows(e, 'SELECT orphaned_at FROM w_owners WHERE owner = ?', 'e:' + UUID1)[0].orphaned_at, null, 'e: не помечается');
});

test('Ely.by без elyId: ely_bind, привязка оставляет один ключ', async () => {
  const e = env();
  e.SKINS.m.set('user:ely_2', JSON.stringify({ name: 'Ely_2', hash: sha(TA), hashes: [sha(TA), sha(TB)], ely: true, created: 1 }));
  const r = await list(e, as('Ely_2', TA));
  assert.equal(r.status, 403);
  assert.equal(r.data.code, 'ely_bind');
  const b = await reg(e, 'Ely_2', TA, { ely: UUID1, bind: true });
  assert.equal(b.status, 200);
  assert.equal(b.data.cloud, true);
  assert.equal((await list(e, as('Ely_2', TA))).status, 200);
  assert.equal((await list(e, as('Ely_2', TB))).status, 403, 'ключ прежнего хозяина имени выброшен');
  // другой ПК того же игрока подтверждается заново и получает те же миры
  await upload(e, as('Ely_2', TA), { data: bytes(1100) });
  await reg(e, 'Ely_2', TC, { ely: UUID1 });
  assert.equal((await list(e, as('Ely_2', TC))).data.worlds.length, 1);
});

test('Ely.by без elyId: обычное подтверждение (без bind) тоже сбрасывает ключи', async () => {
  const e = env();
  e.SKINS.m.set('user:ely_3', JSON.stringify({ name: 'Ely_3', hash: sha(TA), hashes: [sha(TA)], ely: true, created: 1 }));
  await reg(e, 'Ely_3', TB, { ely: UUID2 });
  assert.equal((await list(e, as('Ely_3', TA))).status, 403);
  assert.equal((await list(e, as('Ely_3', TB))).status, 200);
});

test('Ely.by не прислал UUID: облако не включается, ключи как раньше', async () => {
  const e = env();
  const r = await reg(e, 'Ely_4', TA, { ely: 'noid-12345678' });
  assert.equal(r.data.cloud, false);
  assert.equal((await list(e, as('Ely_4', TA))).data.code, 'ely_bind');
});

test('оффлайн-ник забрал Ely.by: прежние миры помечены и удаляются через 7 дней', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  await upload(e, as('Steve_1', TA), { data: bytes(1100) });
  assert.equal(e.WORLDS.objects.size, 1);
  await reg(e, 'Steve_1', TB, { ely: UUID1 });
  assert.deepEqual((await list(e, as('Steve_1', TB))).data.worlds, []);
  assert.ok(sqlRows(e, 'SELECT orphaned_at FROM w_owners WHERE orphaned_at IS NOT NULL').length === 1);
  assert.ok(e.storage.alarmAt, 'будильник поставлен');
  later(8 * DAY);
  await e.obj.alarm();
  assert.equal(e.WORLDS.objects.size, 0);
  assert.equal(sqlRows(e, 'SELECT * FROM w_worlds').length, 0);
});

test('пометку «потерял ник» снимает удачный запрос позже 10 минут', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  await upload(e, as('Steve_1', TA), { data: bytes(1100) });
  const owner = sqlRows(e, 'SELECT owner FROM w_owners')[0].owner;
  await e.obj.worldsOrphan(owner);
  await list(e, as('Steve_1', TA));
  assert.notEqual(sqlRows(e, 'SELECT orphaned_at FROM w_owners')[0].orphaned_at, null, 'сразу не снимается (задержка KV)');
  later(11 * MIN);
  await list(e, as('Steve_1', TA));
  assert.equal(sqlRows(e, 'SELECT orphaned_at FROM w_owners')[0].orphaned_at, null);
});

/* ---------------------------------------------------------------- версии и конфликты */
test('две загрузки от одной базы: первая принята, вторая conflict, её файл в корзину', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const first = await upload(e, as('Steve_1', TA), { data: bytes(1100) });
  const base = first.data.rid;
  const d1 = bytes(1300, 5), d2 = bytes(1400, 7);
  const b1 = await begin(e, as('Steve_1', TA), { base, data: d1, device: DEV1 });
  later(11 * MIN);   // загрузка ПК1 заброшена — ПК2 может начать свою; ПК1 потом возвращается
  const p1 = await sendParts(e, b1.data, d1);
  const b2 = await begin(e, as('Steve_1', TA), { base, data: d2, device: DEV2 });
  assert.equal(b2.status, 409, 'ПК1 только что слал части — busy');
  assert.equal(b2.data.code, 'busy');
  const c1 = await w(e, as('Steve_1', TA), { action: 'commit', sid: b1.data.sid, parts: p1 });
  assert.equal(c1.status, 200);
  const stale = await begin(e, as('Steve_1', TA), { base, data: d2, device: DEV2 });
  assert.equal(stale.data.code, 'conflict', 'устаревшая база — conflict сразу');
  assert.equal(stale.data.head.rid, c1.data.rid);
});

test('commit после чужого commit: conflict, собранный файл уходит в корзину', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const base = (await upload(e, as('Steve_1', TA), { data: bytes(1100) })).data.rid;
  const d1 = bytes(1300, 5), d2 = bytes(1400, 7);
  const b1 = await begin(e, as('Steve_1', TA), { base, data: d1, device: DEV1 });
  const p1 = await sendParts(e, b1.data, d1);
  later(11 * MIN);
  const b2 = await begin(e, as('Steve_1', TA), { base, data: d2, device: DEV2 });   // ПК1 затих — его загрузка отменена
  assert.equal(b2.status, 200);
  assert.equal((await w(e, as('Steve_1', TA), { action: 'commit', sid: b1.data.sid, parts: p1 })).data.code, 'session');
  const p2 = await sendParts(e, b2.data, d2);
  // пока ПК2 собирает файл, ПК1 успевает отправить свою (другая сессия) — гонка двух commit
  let release;
  e.WORLDS.hooks.beforeComplete = () => new Promise((r) => { release = r; });
  const c2 = w(e, as('Steve_1', TA), { action: 'commit', sid: b2.data.sid, parts: p2 });
  await new Promise((r) => setTimeout(r, 20));
  e.WORLDS.hooks.beforeComplete = null;
  // в это время мир меняется (как будто другой commit прошёл) — двигаем head вручную
  e.storage.sql.exec("UPDATE w_worlds SET head_rid = 'ffffffffffffffff', head_rev = 9 WHERE wid = ?", WID);
  release();
  const r2 = await c2;
  assert.equal(r2.data.code, 'conflict');
  const trashed = sqlRows(e, 'SELECT obj FROM w_trash').map((r) => r.obj);
  assert.ok(trashed.some((o) => o.includes(b2.data.rid)));
});

test('повтор commit даёт тот же ответ; commit во время committing — in_progress', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const data = bytes(2100);
  const b = await begin(e, as('Steve_1', TA), { data });
  const parts = await sendParts(e, b.data, data);
  let release;
  e.WORLDS.hooks.beforeComplete = () => new Promise((r) => { release = r; });
  const first = w(e, as('Steve_1', TA), { action: 'commit', sid: b.data.sid, parts });
  await new Promise((r) => setTimeout(r, 20));
  const mid = await w(e, as('Steve_1', TA), { action: 'commit', sid: b.data.sid, parts });
  assert.equal(mid.data.code, 'in_progress');
  e.WORLDS.hooks.beforeComplete = null;
  release();
  const c1 = await first;
  const c2 = await w(e, as('Steve_1', TA), { action: 'commit', sid: b.data.sid, parts });
  assert.equal(c1.status, 200);
  assert.deepEqual(c2.data, c1.data);
});

test('complete упал, но файл собран — успех; без файла — parts и снова open', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const data = bytes(2100);
  const b = await begin(e, as('Steve_1', TA), { data });
  const parts = await sendParts(e, b.data, data);
  const badParts = parts.map((p) => ({ ...p, etag: 'x' + p.etag.slice(1) }));
  const bad = await w(e, as('Steve_1', TA), { action: 'commit', sid: b.data.sid, parts: badParts });
  assert.equal(bad.data.code, 'parts');
  assert.equal(sqlRows(e, 'SELECT state FROM w_sessions WHERE sid = ?', b.data.sid)[0].state, 'open');
  e.WORLDS.hooks.throwAfterComplete = true;
  const ok = await w(e, as('Steve_1', TA), { action: 'commit', sid: b.data.sid, parts });
  assert.equal(ok.status, 200, JSON.stringify(ok.data));
  assert.equal(ok.data.rev, 1);
});

test('committing после перезапуска объекта доводится повтором commit', async () => {
  for (const assembled of [true, false]) {
    const e = env();
    await reg(e, 'Steve_1', TA);
    const data = bytes(2100);
    const b = await begin(e, as('Steve_1', TA), { data });
    const parts = await sendParts(e, b.data, data);
    const s = sqlRows(e, 'SELECT obj, upload_id FROM w_sessions WHERE sid = ?', b.data.sid)[0];
    if (assembled) await e.WORLDS.resumeMultipartUpload(s.obj, s.upload_id).complete(parts.map((p) => ({ partNumber: p.n, etag: p.etag })));
    e.storage.sql.exec("UPDATE w_sessions SET state = 'committing' WHERE sid = ?", b.data.sid);
    e.restart();
    const c = await w(e, as('Steve_1', TA), { action: 'commit', sid: b.data.sid, parts });
    assert.equal(c.status, 200, `assembled=${assembled}: ${JSON.stringify(c.data)}`);
    assert.equal((await list(e, as('Steve_1', TA))).data.worlds[0].head.rev, 1);
  }
});

test('relation: same / behind / unknown / gone', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const r1 = (await upload(e, as('Steve_1', TA), { data: bytes(1100) })).data.rid;
  const r2 = (await upload(e, as('Steve_1', TA), { base: r1, data: bytes(1200, 3) })).data.rid;
  const rel = async (base) => (await list(e, as('Steve_1', TA), { [WID]: base, [WID2]: r1 })).data;
  assert.equal((await rel(r2)).worlds[0].relation, 'same');
  assert.equal((await rel(r1)).worlds[0].relation, 'behind');
  assert.equal((await rel('0123456789abcdef')).worlds[0].relation, 'unknown');
  assert.deepEqual((await rel(r2)).gone, [WID2]);
  assert.equal((await rel(r2)).worlds[0].prev.rid, r1);
});

test('file по убранной версии — conflict, а не gone', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const r1 = (await upload(e, as('Steve_1', TA), { data: bytes(1100) })).data.rid;
  const r2 = (await upload(e, as('Steve_1', TA), { base: r1, data: bytes(1100, 2) })).data.rid;
  await upload(e, as('Steve_1', TA), { base: r2, data: bytes(1100, 3) });   // r1 убрана (храним одну прошлую)
  const d = await download(e, as('Steve_1', TA), WID, r1);
  assert.equal(d.status, 409);
  assert.equal(d.data.code, 'conflict');
});

test('restore: прошлая версия становится новой текущей; вытеснение не удаляет её файл', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const d1 = bytes(1100), d2 = bytes(1200, 9);
  const r1 = (await upload(e, as('Steve_1', TA), { data: d1 })).data.rid;
  const r2 = (await upload(e, as('Steve_1', TA), { base: r1, data: d2 })).data.rid;
  assert.equal((await w(e, as('Steve_1', TA), { action: 'restore', wid: WID, rid: r1, if_head: r1 })).data.code, 'conflict');
  const rs = await w(e, as('Steve_1', TA), { action: 'restore', wid: WID, rid: r1, if_head: r2 });
  assert.equal(rs.status, 200);
  assert.equal(rs.data.rev, 3);
  const l = await list(e, as('Steve_1', TA), { [WID]: r2 });
  assert.equal(l.data.worlds[0].relation, 'behind');
  assert.equal(l.data.worlds[0].head.size, 1100);
  // прошлая (r2) истекает и вытесняется — файл текущей (бывшей r1) остаётся
  later(4 * DAY);
  await e.obj.alarm();
  const d = await download(e, as('Steve_1', TA), WID, rs.data.rid);
  assert.equal(d.status, 200);
  assert.equal(sha(d.b), sha(d1));
  assert.equal(e.WORLDS.objects.size, 1);
});

/* ---------------------------------------------------------------- части */
test('части: неверный sid, номер, длина, md5; повтор перезаписывает', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const data = bytes(2100);
  const b = (await begin(e, as('Steve_1', TA), { data })).data;
  assert.equal((await putPart(e, hex(32, '9'), 1, data.subarray(0, 1024))).data.code, 'session');
  assert.equal((await putPart(e, b.sid, 4, data.subarray(0, 52))).data.code, 'part');
  assert.equal((await putPart(e, b.sid, 1, data.subarray(0, 1000))).data.code, 'part');
  assert.equal((await putPart(e, b.sid, 3, data.subarray(2048))).status, 200, 'последняя — остаток');
  const wrong = await putPart(e, b.sid, 1, data.subarray(0, 1024), { md5sum: hex(32, '0') });
  assert.equal(wrong.data.code, 'part');
  const p1 = await putPart(e, b.sid, 1, data.subarray(0, 1024));
  const p2 = await putPart(e, b.sid, 2, data.subarray(1024, 2048));
  const p3 = await putPart(e, b.sid, 3, data.subarray(2048));
  const c = await w(e, as('Steve_1', TA), { action: 'commit', sid: b.sid, parts: [p1.data, p2.data, p3.data].map((p) => ({ n: p.n, etag: p.etag })) });
  assert.equal(c.status, 200);
  assert.equal(sha((await download(e, as('Steve_1', TA), WID, c.data.rid)).b), sha(data));
});

test('неверные sid с одного адреса ограничены', async () => {
  const e = env();
  for (let i = 0; i < 50; i++) await putPart(e, hex(32, '9'), 1, bytes(10), { ip: '7.7.7.7' });
  assert.equal((await putPart(e, hex(32, '9'), 1, bytes(10), { ip: '7.7.7.7' })).status, 429);
});

test('предел попыток частей на загрузку: дальше session', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const data = bytes(1000);
  const b = (await begin(e, as('Steve_1', TA), { data })).data;   // 1 часть → 2×1+5 = 7 попыток
  for (let i = 0; i < 7; i++) assert.equal((await putPart(e, b.sid, 1, data)).status, 200);
  assert.equal((await putPart(e, b.sid, 1, data)).data.code, 'session');
});

test('месячный предел операций R2 → full', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const old = W.OPS_MONTH;
  W.OPS_MONTH = 3;
  try {
    const data = bytes(2100);
    const b = (await begin(e, as('Steve_1', TA), { data })).data;   // 1
    assert.equal((await putPart(e, b.sid, 1, data.subarray(0, 1024))).status, 200);   // 2
    assert.equal((await putPart(e, b.sid, 2, data.subarray(1024, 2048))).status, 200);   // 3
    assert.equal((await putPart(e, b.sid, 3, data.subarray(2048))).data.code, 'full');
    assert.equal((await begin(e, as('Steve_1', TA), { wid: WID2, data })).data.code, 'full');
  } finally { W.OPS_MONTH = old; }
});

/* ---------------------------------------------------------------- пределы места */
test('too_big, число миров, квота (прошлые версии уходят первыми), общий предел', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  assert.equal((await begin(e, as('Steve_1', TA), { data: bytes(W.MAX_WORLD + 1) })).data.code, 'too_big');
  // квота 20 КиБ: мир 8 КиБ, вторая версия 8 КиБ (+8 прошлая) = 16, третья версия влезает только без прошлой
  const big = (s) => bytes(8 * 1024, s);
  const r1 = (await upload(e, as('Steve_1', TA), { data: big(1) })).data.rid;
  const r2 = (await upload(e, as('Steve_1', TA), { base: r1, data: big(2) })).data.rid;
  const b3 = await begin(e, as('Steve_1', TA), { wid: WID2, data: big(3) });
  assert.equal(b3.status, 200, JSON.stringify(b3.data));
  assert.equal(sqlRows(e, 'SELECT COUNT(*) AS n FROM w_vers WHERE keep_until IS NOT NULL')[0].n, 0, 'прошлая версия вытеснена');
  const q = await begin(e, as('Steve_1', TA), { wid: hex(24, 'c'), data: big(4) });
  assert.equal(q.data.code, 'quota');
  assert.ok(r2);
  // общий предел: 60 КиБ на сервер
  const e2 = env();
  for (let i = 0; i < 8; i++) {   // 60 КиБ / 8 КиБ — влезает 7 миров
    const nick = 'Pl_' + i;
    await reg(e2, nick, String(i).repeat(40), {}, '10.0.0.' + i);
    const r = await upload(e2, as(nick, String(i).repeat(40)), { data: big(i), ip: '10.0.0.' + i });
    if (i < 7) assert.equal(r.status, 200, JSON.stringify(r.data));
    else assert.equal(r.data.code, 'full');
  }
  const total = sqlRows(e2, 'SELECT SUM(size) AS s FROM w_vers')[0].s;
  assert.ok(total <= W.GLOBAL, 'больше общего предела не принято');
});

test('миров на владельца не больше W.WORLDS', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  for (let i = 0; i < W.WORLDS; i++) assert.equal((await upload(e, as('Steve_1', TA), { wid: hex(23, 'a') + i, data: bytes(100, i) })).status, 200);
  const r = await begin(e, as('Steve_1', TA), { wid: hex(23, 'b') + '9', data: bytes(100) });
  assert.equal(r.data.code, 'quota');
  assert.equal(r.data.reason, 'worlds');
});

test('новых облачных владельцев с одного адреса — не больше 3 в сутки', async () => {
  const e = env();
  for (let i = 0; i < 4; i++) await reg(e, 'Pl_' + i, String(i).repeat(40), {}, '10.0.0.' + i);
  for (let i = 0; i < 3; i++) assert.equal((await begin(e, as('Pl_' + i, String(i).repeat(40)), { data: bytes(100), ip: '5.5.5.5' })).status, 200);
  const r = await w(e, as('Pl_3', '3'.repeat(40)), { action: 'begin', wid: WID, base: '', size: 100, sha256: hex(64), device: DEV1, new: true }, '5.5.5.5');
  assert.equal(r.status, 429);
});

/* ---------------------------------------------------------------- загрузки */
test('повтор begin нового мира с тем же wid — один мир; busy не тратит суточный лимит', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const data = bytes(1500);
  const a = await begin(e, as('Steve_1', TA), { data });
  const b = await begin(e, as('Steve_1', TA), { data });   // ответ на первый потерялся
  assert.equal(b.status, 200);
  assert.notEqual(a.data.sid, b.data.sid);
  assert.equal(sqlRows(e, 'SELECT COUNT(*) AS n FROM w_worlds')[0].n, 1);
  assert.equal((await putPart(e, a.data.sid, 1, data.subarray(0, 1024))).data.code, 'session', 'прежняя загрузка отменена');
  assert.equal(e.WORLDS.uploads.size, 1);
  const begins = () => sqlRows(e, "SELECT n FROM w_count WHERE k LIKE 'begin:%'")[0].n;
  const before = begins();
  for (let i = 0; i < 5; i++) assert.equal((await begin(e, as('Steve_1', TA), { data, device: DEV2 })).data.code, 'busy');
  assert.equal(begins(), before);
});

test('abort по wid снимает загрузку другого ПК; истёкшая загрузка отменяется будильником', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const data = bytes(1500);
  await begin(e, as('Steve_1', TA), { data, device: DEV2 });
  assert.equal((await list(e, as('Steve_1', TA))).data.worlds[0].upload.device_name, 'ПК 2');
  assert.equal((await w(e, as('Steve_1', TA), { action: 'abort', wid: WID })).status, 200);
  assert.equal(e.WORLDS.uploads.size, 0);
  assert.equal((await list(e, as('Steve_1', TA))).data.usage.used, 0, 'резерв снят');
  await begin(e, as('Steve_1', TA), { wid: WID2, data });
  later(25 * HOUR);
  await e.obj.alarm();
  assert.equal(e.WORLDS.uploads.size, 0);
  assert.equal(sqlRows(e, 'SELECT COUNT(*) AS n FROM w_sessions')[0].n, 0);
  assert.equal(sqlRows(e, 'SELECT COUNT(*) AS n FROM w_worlds')[0].n, 0, 'пустые миры убраны');
});

test('неудачный abort в R2 держит место, будильник повторяет', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  await begin(e, as('Steve_1', TA), { data: bytes(1500) });
  e.WORLDS.hooks.failAbort = true;
  await w(e, as('Steve_1', TA), { action: 'abort', wid: WID });
  assert.equal((await list(e, as('Steve_1', TA))).data.usage.used, 1500);
  e.WORLDS.hooks.failAbort = false;
  await e.obj.alarm();
  assert.equal((await list(e, as('Steve_1', TA))).data.usage.used, 0);
});

test('delete убирает мир и файлы, на ПК ничего не трогаем', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  const r1 = (await upload(e, as('Steve_1', TA), { data: bytes(1100) })).data.rid;
  await upload(e, as('Steve_1', TA), { base: r1, data: bytes(1100, 2) });
  assert.equal((await w(e, as('Steve_1', TA), { action: 'delete', wid: WID })).status, 200);
  assert.deepEqual((await list(e, as('Steve_1', TA), { [WID]: r1 })).data.gone, [WID]);
  await e.obj.alarm();
  assert.equal(e.WORLDS.objects.size, 0);
});

/* ---------------------------------------------------------------- отметки */
test('lease: свою не видно, чужую видно, истекает', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  await upload(e, as('Steve_1', TA), { data: bytes(1100) });
  const l1 = await w(e, as('Steve_1', TA), { action: 'lease', device: DEV1, device_name: 'Ноутбук', wids: [WID] });
  assert.deepEqual(l1.data.leases, {});
  const l2 = await w(e, as('Steve_1', TA), { action: 'lease', device: DEV2, device_name: 'Комп', wids: [WID] });
  assert.equal(l2.data.leases[WID].device_name, 'Ноутбук');
  assert.equal((await list(e, as('Steve_1', TA), {}, DEV2)).data.worlds[0].lease.device_name, 'Ноутбук');
  await w(e, as('Steve_1', TA), { action: 'release', device: DEV1, wids: [WID] });
  assert.equal((await list(e, as('Steve_1', TA), {}, DEV2)).data.worlds[0].lease, null);
  await w(e, as('Steve_1', TA), { action: 'lease', device: DEV1, device_name: 'Ноутбук', wids: [WID] });
  later(21 * MIN);
  assert.equal((await list(e, as('Steve_1', TA), {}, DEV2)).data.worlds[0].lease, null);
});

/* ---------------------------------------------------------------- уборка */
test('сверка бакета удаляет только сирот старше суток', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  await upload(e, as('Steve_1', TA), { data: bytes(1100) });
  e.WORLDS.objects.set('w/0000000000000000/orphan/old.zip', { b: bytes(10), uploaded: new Date(Date.now() - 2 * DAY) });
  e.WORLDS.objects.set('w/0000000000000000/orphan/new.zip', { b: bytes(10), uploaded: new Date(Date.now()) });
  await e.obj.alarm();
  assert.ok(!e.WORLDS.objects.has('w/0000000000000000/orphan/old.zip'));
  assert.ok(e.WORLDS.objects.has('w/0000000000000000/orphan/new.zip'));
  assert.equal(e.WORLDS.objects.size, 2, 'файл мира на месте');
});

test('будильник не отодвигается частыми событиями', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  await begin(e, as('Steve_1', TA), { data: bytes(1100) });
  const first = e.storage.alarmAt;
  later(30 * MIN);
  await begin(e, as('Steve_1', TA), { wid: WID2, data: bytes(1100) });
  assert.equal(e.storage.alarmAt, first);
});

test('90 дней без облака — миры удаляются', async () => {
  const e = env();
  await reg(e, 'Steve_1', TA);
  await upload(e, as('Steve_1', TA), { data: bytes(1100) });
  later(91 * DAY);
  await e.obj.alarm();
  assert.equal(e.WORLDS.objects.size, 0);
});

/* ---------------------------------------------------------------- счётчики переживают перезапуск */
test('неверные пароли считаются в SQLite и переживают перезапуск объекта', async () => {
  const e = env();
  await reg(e, 'Victim_1', TA);
  for (let i = 0; i < 20; i++) {
    await post(e, '/api/presence', { ...as('Victim_1', TB), status: null }, '6.6.6.6');
    if (i % 5 === 4) e.restart();   // объект заснул и проснулся
  }
  assert.equal((await post(e, '/api/presence', { ...as('Victim_1', TA), status: null }, '6.6.6.6')).status, 429);
  later(11 * MIN);
  assert.equal((await post(e, '/api/presence', { ...as('Victim_1', TA), status: null }, '6.6.6.6')).status, 200);
});

test('сводка stats показывает облако', async () => {
  const e = env();
  e.STATS_KEY = 'k'.repeat(20);
  await reg(e, 'Steve_1', TA);
  await upload(e, as('Steve_1', TA), { data: bytes(1100) });
  const r = await (await worker.fetch(new Request('https://x.test/api/stats', { headers: { 'x-stats-key': e.STATS_KEY } }), e)).json();
  assert.equal(r.worlds.worlds, 1);
  assert.equal(r.worlds.bytes, 1100);
});

test('суточные лимиты (ники с адреса) переживают перезапуск объекта', async () => {
  const e = env();
  for (let i = 0; i < 10; i++) {
    assert.equal((await reg(e, 'Lim_' + i, String(i).padStart(4, '0').repeat(10), {}, '7.7.7.7')).status, 200);
    if (i === 4) e.restart();
  }
  e.restart();
  assert.equal((await reg(e, 'Lim_10', '9'.repeat(40), {}, '7.7.7.7')).status, 429);
});
