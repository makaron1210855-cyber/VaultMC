// Тесты сервера скинов (server/worker.js): поддельный KV и поддельный Durable Object на встроенном SQLite Node.
// Запуск: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import worker, { Social } from '../server/worker.js';

function fakeKV() {
  const m = new Map();
  return {
    m,
    async get(k, type) {
      await new Promise((r) => setImmediate(r));   // как настоящий KV: ответ приходит не сразу
      if (!m.has(k)) return null;
      const v = m.get(k);
      return type === 'json' || (type && type.type === 'json') ? JSON.parse(v) : v;
    },
    async put(k, v) { m.set(k, String(v)); },
    async delete(k) { m.delete(k); },
  };
}

// state.storage.sql Durable Object поверх node:sqlite: exec(запрос, ...параметры).toArray()
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

function env() {
  const e = { SKINS: fakeKV() };
  const obj = new Social({ storage: { sql: fakeSql() } }, e);
  e.SOCIAL = {
    idFromName: (n) => n,
    get: () => ({ fetch: (url, init) => obj.fetch(new Request(url, init)) }),
  };
  return e;
}

const TOKEN_A = 'a'.repeat(40);
const TOKEN_B = 'b'.repeat(40);
const SHA = 'c'.repeat(40);
const tok = (i) => String(i).padStart(4, '0').repeat(10);

async function post(e, path, body, ip = '1.1.1.1') {
  const req = new Request('https://x.test' + path, {
    method: 'POST', headers: { 'content-type': 'application/json', 'CF-Connecting-IP': ip }, body: JSON.stringify(body),
  });
  const res = await worker.fetch(req, e);
  return { status: res.status, data: await res.json() };
}

async function get(e, path, ip = '1.1.1.1') {
  const res = await worker.fetch(new Request('https://x.test' + path, { headers: { 'CF-Connecting-IP': ip } }), e);
  return { status: res.status, data: await res.json() };
}

const reg = (e, name, token, ip) => post(e, '/api/register', { name, token }, ip);
const as = (name, token) => ({ name, token });

/* ---------------------------------------------------------------- ники */
test('регистрация свободного ника проходит', async () => {
  const e = env();
  const r = await reg(e, 'Steve_1', TOKEN_A);
  assert.equal(r.status, 200);
  assert.equal(r.data.ok, true);
});

test('чужой ключ на занятый ник отклоняется (409)', async () => {
  const e = env();
  await reg(e, 'Steve_1', TOKEN_A);
  const r = await reg(e, 'Steve_1', TOKEN_B);
  assert.equal(r.status, 409);
  assert.equal(r.data.code, 'taken');
});

test('короткий ключ и кривой ник отклоняются', async () => {
  const e = env();
  assert.equal((await post(e, '/api/register', { name: 'Steve_1', token: 'short' })).status, 400);
  assert.equal((await post(e, '/api/register', { name: 'ab', token: TOKEN_A })).status, 400);
});

test('лимит новых ников с одного адреса, повторный вход не считается', async () => {
  const e = env();
  for (let i = 0; i < 10; i++) assert.equal((await reg(e, 'Nick_' + i, tok(i), '9.9.9.9')).status, 200);
  for (let i = 0; i < 5; i++) assert.equal((await reg(e, 'Nick_0', tok(0), '9.9.9.9')).data.already, true);
  const over = await reg(e, 'Nick_10', tok(10), '9.9.9.9');
  assert.equal(over.status, 429);
  assert.equal((await reg(e, 'Nick_11', tok(11), '8.8.8.8')).status, 200, 'другой адрес не задет');
});

test('после 20 неверных паролей вход блокируется даже с верным', async () => {
  const e = env();
  await reg(e, 'Victim_1', TOKEN_A);
  for (let i = 0; i < 20; i++) {
    const r = await post(e, '/api/presence', { ...as('Victim_1', TOKEN_B), status: null }, '6.6.6.6');
    assert.equal(r.status, 403);
    assert.equal(r.data.code, 'auth');
  }
  const locked = await post(e, '/api/presence', { ...as('Victim_1', TOKEN_A), status: null }, '6.6.6.6');
  assert.equal(locked.status, 429);
  const other = await post(e, '/api/presence', { ...as('Victim_1', TOKEN_A), status: null }, '5.5.5.5');
  assert.equal(other.status, 200, 'игрок с другого адреса не заблокирован');
});

test('ошибка сервера не отдаёт подробности наружу', async () => {
  const e = env();
  e.SKINS.get = async () => { throw new Error('секретная подробность'); };
  const r = await post(e, '/api/presence', { ...as('Steve_1', TOKEN_A), status: null });
  assert.equal(r.status, 500);
  assert.ok(!JSON.stringify(r.data).includes('секретная'));
});

/* ---------------------------------------------------------------- комнаты */
test('комната: открыть, посмотреть, закрыть', async () => {
  const e = env();
  await reg(e, 'Host_1', TOKEN_A);
  const open = await post(e, '/api/room', { ...as('Host_1', TOKEN_A), action: 'open', info: { instance: 'main' } });
  assert.equal(open.status, 200);
  assert.match(open.data.code, /^[A-Z0-9]{6}$/);

  const seen = await get(e, '/api/room/' + open.data.code);
  assert.equal(seen.status, 200);
  assert.equal(seen.data.host, 'Host_1');
  assert.equal(seen.data.info.instance, 'main');

  await post(e, '/api/room', { ...as('Host_1', TOKEN_A), action: 'close', code: open.data.code });
  const gone = await get(e, '/api/room/' + open.data.code);
  assert.equal(gone.status, 404);
  assert.equal(gone.data.code, 'gone');
});

test('чужой игрок не может менять чужую комнату', async () => {
  const e = env();
  await reg(e, 'Host_1', TOKEN_A);
  await reg(e, 'Other_2', TOKEN_B);
  const open = await post(e, '/api/room', { ...as('Host_1', TOKEN_A), action: 'open', info: {} });
  const r = await post(e, '/api/room', { ...as('Other_2', TOKEN_B), action: 'close', code: open.data.code });
  assert.equal(r.status, 200);
  assert.equal(r.data.gone, true);
  assert.equal((await get(e, '/api/room/' + open.data.code)).status, 200, 'комната не должна закрыться');
});

test('список модов в комнате очищается: плохой sha1 отбрасывается', async () => {
  const e = env();
  await reg(e, 'Host_1', TOKEN_A);
  const open = await post(e, '/api/room', {
    ...as('Host_1', TOKEN_A), action: 'open',
    info: { mods: [
      { id: 'sodium', name: 'Sodium', sha1: SHA, size: 100 },
      { id: 'BAD ID!', name: 'x', sha1: SHA, size: 1 },
      { id: 'nohash', name: 'y', sha1: 'not-a-hash', size: 1 },
    ] },
  });
  const seen = await get(e, '/api/room/' + open.data.code);
  assert.deepEqual(seen.data.info.mods.map((m) => m.id), ['sodium']);
});

test('данные ретранслятора проходят в комнату и статус, кривые отбрасываются', async () => {
  const e = env();
  await reg(e, 'Host_1', TOKEN_A);
  const relay = { port: 25601, token: 'f'.repeat(32) };
  const open = await post(e, '/api/room', { ...as('Host_1', TOKEN_A), action: 'open', info: { relay } });
  assert.deepEqual((await get(e, '/api/room/' + open.data.code)).data.info.relay, relay);
  const bad = await post(e, '/api/room', { ...as('Host_1', TOKEN_A), action: 'open', info: { relay: { port: '1; rm', token: 'zz' } } });
  assert.equal((await get(e, '/api/room/' + bad.data.code)).data.info.relay, null);

  await reg(e, 'Friend_2', TOKEN_B);
  await post(e, '/api/friends', { ...as('Friend_2', TOKEN_B), action: 'request', other: 'Host_1' });
  await post(e, '/api/friends', { ...as('Host_1', TOKEN_A), action: 'accept', other: 'Friend_2' });
  await post(e, '/api/presence', { ...as('Host_1', TOKEN_A), status: { state: 'playing', relay } });
  const list = await post(e, '/api/friends', { ...as('Friend_2', TOKEN_B), action: 'list' });
  assert.deepEqual(list.data.status.Host_1.relay, relay);
});

test('краткие сведения о сборке для страницы сайта: без файлов, с CORS', async () => {
  const e = env();
  await reg(e, 'Maker_1', TOKEN_A);
  const pack = { name: 'Мои моды', loader: 'fabric', game_version: '1.20.1', overrides: 'SECRETDATA',
    index: { files: [{ path: 'mods/sodium.jar' }, { path: 'mods/iris.jar' }, { path: 'resourcepacks/x.zip' }] } };
  const sh = await post(e, '/api/share', { ...as('Maker_1', TOKEN_A), pack });
  const res = await worker.fetch(new Request(`https://x.test/api/share/${sh.data.code}?brief=1`), e);
  assert.equal(res.headers.get('access-control-allow-origin'), '*');
  const d = await res.json();
  assert.deepEqual([d.name, d.loader, d.game_version, d.mods, d.by], ['Мои моды', 'fabric', '1.20.1', 2, 'Maker_1']);
  assert.ok(!JSON.stringify(d).includes('SECRETDATA'));
  const miss = await worker.fetch(new Request('https://x.test/api/share/ZZZZZZ?brief=1'), e);
  assert.equal(miss.status, 404);
  assert.equal(miss.headers.get('access-control-allow-origin'), '*');
});

test('неверный ключ не открывает комнату (403)', async () => {
  const e = env();
  await reg(e, 'Host_1', TOKEN_A);
  const r = await post(e, '/api/room', { ...as('Host_1', TOKEN_B), action: 'open', info: {} });
  assert.equal(r.status, 403);
});

/* ---------------------------------------------------------------- друзья */
test('заявка, принятие и статус друга в списке', async () => {
  const e = env();
  await reg(e, 'Alice', TOKEN_A);
  await reg(e, 'Bob_2', TOKEN_B);
  const req = await post(e, '/api/friends', { ...as('Alice', TOKEN_A), action: 'request', other: 'bob_2' });
  assert.deepEqual(req.data.outgoing, ['Bob_2']);
  const acc = await post(e, '/api/friends', { ...as('Bob_2', TOKEN_B), action: 'accept', other: 'Alice' });
  assert.deepEqual(acc.data.friends, ['Alice']);
  await post(e, '/api/presence', { ...as('Bob_2', TOKEN_B), status: { state: 'playing', version: '1.20.1' } });
  const list = await post(e, '/api/friends', { ...as('Alice', TOKEN_A), action: 'list' });
  assert.deepEqual(list.data.friends, ['Bob_2']);
  assert.equal(list.data.status.Bob_2.state, 'playing');
});

test('друзья старого сервера переносятся из KV, изменения копируются обратно', async () => {
  const e = env();
  await reg(e, 'Alice', TOKEN_A);
  await reg(e, 'Bob_2', TOKEN_B);
  await reg(e, 'Carol', 'c'.repeat(40));
  e.SKINS.m.set('friends:alice', JSON.stringify({ friends: ['Bob_2'], incoming: ['Carol'], outgoing: [] }));
  e.SKINS.m.set('friends:bob_2', JSON.stringify({ friends: ['Alice'], incoming: [], outgoing: [] }));
  const list = await post(e, '/api/friends', { ...as('Alice', TOKEN_A), action: 'list' });
  assert.deepEqual(list.data.friends, ['Bob_2']);
  assert.deepEqual(list.data.incoming, ['Carol']);

  await post(e, '/api/friends', { ...as('Alice', TOKEN_A), action: 'remove', other: 'Bob_2' });
  assert.deepEqual(JSON.parse(e.SKINS.m.get('friends:alice')).friends, []);
  assert.deepEqual(JSON.parse(e.SKINS.m.get('friends:bob_2')).friends, []);
  // повторный перенос не возвращает удалённого друга
  const again = await post(e, '/api/friends', { ...as('Alice', TOKEN_A), action: 'list' });
  assert.deepEqual(again.data.friends, []);
});

test('40 одновременных заявок одному игроку: не теряется ни одна', async () => {
  const e = env();
  await reg(e, 'Star', TOKEN_A);
  const fans = Array.from({ length: 40 }, (_, i) => ['Fan_' + i, tok(i)]);
  for (const [n, t] of fans) await reg(e, n, t, '10.0.0.' + Math.floor(Number(n.slice(4)) / 9));
  const results = await Promise.all(fans.map(([n, t]) => post(e, '/api/friends', { ...as(n, t), action: 'request', other: 'Star' })));
  assert.ok(results.every((r) => r.status === 200));
  const list = await post(e, '/api/friends', { ...as('Star', TOKEN_A), action: 'list' });
  assert.equal(list.data.incoming.length, 40);
});

/* ---------------------------------------------------------------- аккаунты: пароль, код восстановления, смена ника */
const TOKEN_C = 'c'.repeat(40);
const CODE = 'ABCDEFGHJKLMNPQR';

test('смена пароля закрывает старый ключ', async () => {
  const e = env();
  await reg(e, 'Steve_1', TOKEN_A);
  const ch = await post(e, '/api/register', { name: 'Steve_1', token: TOKEN_B, old: TOKEN_A, replace: true });
  assert.equal(ch.status, 200);
  assert.equal((await post(e, '/api/presence', { ...as('Steve_1', TOKEN_B), status: null })).status, 200);
  const old = await post(e, '/api/presence', { ...as('Steve_1', TOKEN_A), status: null });
  assert.equal(old.status, 403, 'старый пароль больше не действует');
});

test('без replace (вход на другом ПК) прежний ключ остаётся', async () => {
  const e = env();
  await reg(e, 'Steve_1', TOKEN_A);
  await post(e, '/api/register', { name: 'Steve_1', token: TOKEN_B, old: TOKEN_A });
  assert.equal((await post(e, '/api/presence', { ...as('Steve_1', TOKEN_A), status: null })).status, 200);
});

test('код восстановления: сброс пароля, код одноразовый, старые ключи не действуют', async () => {
  const e = env();
  await reg(e, 'Steve_1', TOKEN_A);
  assert.equal((await post(e, '/api/recovery', { ...as('Steve_1', TOKEN_A), code: 'ABCD-EFGH-JKLM-NPQR' })).status, 200);
  assert.ok(!e.SKINS.m.get('user:steve_1').includes(CODE), 'сервер хранит не сам код');

  const bad = await post(e, '/api/register', { name: 'Steve_1', token: TOKEN_C, recovery: 'ZZZZZZZZZZZZZZZZ' });
  assert.equal(bad.status, 403);
  assert.equal(bad.data.code, 'recovery');

  const ok = await post(e, '/api/register', { name: 'Steve_1', token: TOKEN_C, recovery: 'abcd efgh jklm npqr' });
  assert.equal(ok.data.recovered, true);
  assert.equal((await post(e, '/api/presence', { ...as('Steve_1', TOKEN_C), status: null })).status, 200);
  assert.equal((await post(e, '/api/presence', { ...as('Steve_1', TOKEN_A), status: null }, '2.2.2.2')).status, 403);
  const again = await post(e, '/api/register', { name: 'Steve_1', token: TOKEN_B, recovery: CODE });
  assert.equal(again.status, 403, 'код одноразовый');
});

test('подбор кода восстановления блокируется', async () => {
  const e = env();
  await reg(e, 'Steve_1', TOKEN_A);
  await post(e, '/api/recovery', { ...as('Steve_1', TOKEN_A), code: CODE });
  for (let i = 0; i < 20; i++) {
    const r = await post(e, '/api/register', { name: 'Steve_1', token: 'x'.repeat(38) + String(i).padStart(2, '0'), recovery: 'Z'.repeat(15) + 'A' }, '6.6.6.6');
    assert.equal(r.status, 403);
  }
  const r = await post(e, '/api/register', { name: 'Steve_1', token: TOKEN_C, recovery: CODE }, '6.6.6.6');
  assert.equal(r.status, 429, 'после 20 неверных попыток с адреса — блокировка, даже с верным кодом');
  const other = await post(e, '/api/register', { name: 'Steve_1', token: TOKEN_C, recovery: CODE }, '7.7.7.7');
  assert.equal(other.data.recovered, true, 'хозяин с другого адреса восстанавливает');
});

test('смена ника: друзья, скин, код восстановления переезжают, старый ник свободен', async () => {
  const e = env();
  await reg(e, 'Old_Nick', TOKEN_A);
  await reg(e, 'Buddy', TOKEN_B);
  await post(e, '/api/friends', { ...as('Buddy', TOKEN_B), action: 'request', other: 'Old_Nick' });
  await post(e, '/api/friends', { ...as('Old_Nick', TOKEN_A), action: 'accept', other: 'Buddy' });
  e.SKINS.m.set('skin:old_nick', JSON.stringify({ png: 'AAAA', slim: false, hash: 'h' }));
  await post(e, '/api/recovery', { ...as('Old_Nick', TOKEN_A), code: CODE });

  const r = await post(e, '/api/rename', { ...as('Old_Nick', TOKEN_A), new: 'New_Nick', newToken: TOKEN_C });
  assert.equal(r.status, 200);
  assert.equal(r.data.name, 'New_Nick');
  assert.ok(e.SKINS.m.get('skin:new_nick'));
  assert.ok(!e.SKINS.m.get('skin:old_nick'));
  assert.ok(!e.SKINS.m.get('user:old_nick'));
  const buddy = await post(e, '/api/friends', { ...as('Buddy', TOKEN_B), action: 'list' });
  assert.deepEqual(buddy.data.friends, ['New_Nick']);
  const me = await post(e, '/api/friends', { ...as('New_Nick', TOKEN_C), action: 'list' });
  assert.deepEqual(me.data.friends, ['Buddy']);
  // код восстановления переехал
  const rec = await post(e, '/api/register', { name: 'New_Nick', token: 'd'.repeat(40), recovery: CODE });
  assert.equal(rec.data.recovered, true);
  // старый ник может занять кто угодно — и он не получает чужих друзей
  await reg(e, 'Old_Nick', 'e'.repeat(40), '3.3.3.3');
  const stranger = await post(e, '/api/friends', { ...as('Old_Nick', 'e'.repeat(40)), action: 'list' }, '3.3.3.3');
  assert.deepEqual(stranger.data.friends, []);
});

test('смена ника: занятый ник и ник Ely.by отклоняются', async () => {
  const e = env();
  await reg(e, 'Nick_A', TOKEN_A);
  await reg(e, 'Nick_B', TOKEN_B);
  const taken = await post(e, '/api/rename', { ...as('Nick_A', TOKEN_A), new: 'nick_b', newToken: TOKEN_C });
  assert.equal(taken.status, 409);
  e.SKINS.m.set('user:ely_one', JSON.stringify({ name: 'Ely_One', hash: 'x', hashes: [], ely: true }));
  const ely = JSON.parse(e.SKINS.m.get('user:ely_one'));
  assert.equal(ely.ely, true);
});

test('смена ника: друг ещё со старого сервера (KV) не уносит старый ник', async () => {
  const e = env();
  await reg(e, 'Old_Nick', TOKEN_A);
  await reg(e, 'Legacy', TOKEN_B);
  e.SKINS.m.set('friends:old_nick', JSON.stringify({ friends: ['Legacy'], incoming: [], outgoing: [] }));
  e.SKINS.m.set('friends:legacy', JSON.stringify({ friends: ['Old_Nick'], incoming: [], outgoing: [] }));
  await post(e, '/api/rename', { ...as('Old_Nick', TOKEN_A), new: 'New_Nick', newToken: TOKEN_C });
  const legacy = await post(e, '/api/friends', { ...as('Legacy', TOKEN_B), action: 'list' });
  assert.deepEqual(legacy.data.friends, ['New_Nick']);
});

/* ---------------------------------------------------------------- прямое соединение */
async function friendsPair(e) {
  await reg(e, 'Host_1', TOKEN_A);
  await reg(e, 'Guest_2', TOKEN_B);
  await post(e, '/api/friends', { ...as('Guest_2', TOKEN_B), action: 'request', other: 'Host_1' });
  await post(e, '/api/friends', { ...as('Host_1', TOKEN_A), action: 'accept', other: 'Guest_2' });
}

test('P2P: приглашение, ответ хозяина, гость получает ответ', async () => {
  const e = env();
  await friendsPair(e);
  const offer = await post(e, '/api/p2p', { ...as('Guest_2', TOKEN_B), action: 'offer', host: 'Host_1', id: 'abcdefgh1', sdp: { type: 'offer', sdp: 'x' } });
  assert.equal(offer.status, 200);
  const inbox = await post(e, '/api/p2p', { ...as('Host_1', TOKEN_A), action: 'inbox' });
  assert.equal(inbox.data.offers.length, 1);
  assert.equal(inbox.data.offers[0].from, 'Guest_2');
  assert.deepEqual(inbox.data.offers[0].sdp, { type: 'offer', sdp: 'x' });
  await post(e, '/api/p2p', { ...as('Host_1', TOKEN_A), action: 'answer', id: 'abcdefgh1', sdp: { type: 'answer', sdp: 'y' } });
  const poll = await post(e, '/api/p2p', { ...as('Guest_2', TOKEN_B), action: 'poll', host: 'Host_1', id: 'abcdefgh1' });
  assert.deepEqual(poll.data.answer.sdp, { type: 'answer', sdp: 'y' });
});

test('P2P: ответ хозяина не видит посторонний', async () => {
  const e = env();
  await friendsPair(e);
  await reg(e, 'Spy_3', 'd'.repeat(40));
  await post(e, '/api/p2p', { ...as('Guest_2', TOKEN_B), action: 'offer', host: 'Host_1', id: 'abcdefgh1', sdp: 'x' });
  await post(e, '/api/p2p', { ...as('Host_1', TOKEN_A), action: 'answer', id: 'abcdefgh1', sdp: 'y' });
  const spy = await post(e, '/api/p2p', { ...as('Spy_3', 'd'.repeat(40)), action: 'poll', host: 'Host_1', id: 'abcdefgh1' });
  assert.equal(spy.data.answer, null);
});

test('P2P: не друг без кода комнаты не может постучаться, с кодом — может', async () => {
  const e = env();
  await reg(e, 'Host_1', TOKEN_A);
  await reg(e, 'Guest_2', TOKEN_B);
  const denied = await post(e, '/api/p2p', { ...as('Guest_2', TOKEN_B), action: 'offer', host: 'Host_1', id: 'abcdefgh1', sdp: 'x' });
  assert.equal(denied.status, 403);
  assert.equal(denied.data.code, 'not_friend');
  const open = await post(e, '/api/room', { ...as('Host_1', TOKEN_A), action: 'open', info: { p2p: true } });
  const ok = await post(e, '/api/p2p', { ...as('Guest_2', TOKEN_B), action: 'offer', host: 'Host_1', id: 'abcdefgh1', sdp: 'x', code: open.data.code });
  assert.equal(ok.status, 200);
});

test('P2P: 10 одновременных приглашений одному хозяину — все доходят', async () => {
  const e = env();
  await reg(e, 'Host_1', TOKEN_A);
  const open = await post(e, '/api/room', { ...as('Host_1', TOKEN_A), action: 'open', info: { p2p: true } });
  const guests = Array.from({ length: 10 }, (_, i) => ['Guest_' + i, tok(100 + i)]);
  for (const [n, t] of guests) await reg(e, n, t, '20.0.0.' + n.slice(6));
  const res = await Promise.all(guests.map(([n, t], i) => post(e, '/api/p2p', {
    ...as(n, t), action: 'offer', host: 'Host_1', id: 'guestid' + String(i).padStart(3, '0'), sdp: 'x', code: open.data.code,
  })));
  assert.ok(res.every((r) => r.status === 200));
  const inbox = await post(e, '/api/p2p', { ...as('Host_1', TOKEN_A), action: 'inbox' });
  assert.equal(inbox.data.offers.length, 10);
});

test('P2P: ответ на чужое или несуществующее приглашение не сохраняется', async () => {
  const e = env();
  await friendsPair(e);
  const r = await post(e, '/api/p2p', { ...as('Host_1', TOKEN_A), action: 'answer', id: 'nosuchid1', sdp: 'y' });
  assert.equal(r.data.ignored, true);
});
