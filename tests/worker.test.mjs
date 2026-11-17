// Тесты сервера скинов (server/worker.js) на поддельном KV.
// Запуск: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import worker from '../server/worker.js';

function fakeKV() {
  const m = new Map();
  return {
    m,
    async get(k, type) {
      if (!m.has(k)) return null;
      const v = m.get(k);
      return type === 'json' ? JSON.parse(v) : v;
    },
    async put(k, v) { m.set(k, String(v)); },
    async delete(k) { m.delete(k); },
  };
}

const env = () => ({ SKINS: fakeKV() });
const TOKEN_A = 'a'.repeat(40);
const TOKEN_B = 'b'.repeat(40);
const SHA = 'c'.repeat(40);

async function post(e, path, body) {
  const req = new Request('https://x.test' + path, {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  const res = await worker.fetch(req, e);
  return { status: res.status, data: await res.json() };
}

async function get(e, path) {
  const res = await worker.fetch(new Request('https://x.test' + path), e);
  return { status: res.status, data: await res.json() };
}

test('регистрация свободного ника проходит', async () => {
  const e = env();
  const r = await post(e, '/api/register', { name: 'Steve_1', token: TOKEN_A });
  assert.equal(r.status, 200);
  assert.equal(r.data.ok, true);
});

test('чужой ключ на занятый ник отклоняется (409)', async () => {
  const e = env();
  await post(e, '/api/register', { name: 'Steve_1', token: TOKEN_A });
  const r = await post(e, '/api/register', { name: 'Steve_1', token: TOKEN_B });
  assert.equal(r.status, 409);
  assert.equal(r.data.code, 'taken');
});

test('короткий ключ и кривой ник отклоняются', async () => {
  const e = env();
  assert.equal((await post(e, '/api/register', { name: 'Steve_1', token: 'short' })).status, 400);
  assert.equal((await post(e, '/api/register', { name: 'ab', token: TOKEN_A })).status, 400);
});

test('комната: открыть, посмотреть, закрыть', async () => {
  const e = env();
  await post(e, '/api/register', { name: 'Host_1', token: TOKEN_A });
  const open = await post(e, '/api/room', { name: 'Host_1', token: TOKEN_A, action: 'open', info: { instance: 'main' } });
  assert.equal(open.status, 200);
  assert.match(open.data.code, /^[A-Z0-9]{6}$/);

  const seen = await get(e, '/api/room/' + open.data.code);
  assert.equal(seen.status, 200);
  assert.equal(seen.data.host, 'Host_1');

  await post(e, '/api/room', { name: 'Host_1', token: TOKEN_A, action: 'close', code: open.data.code });
  const gone = await get(e, '/api/room/' + open.data.code);
  assert.equal(gone.status, 404);
  assert.equal(gone.data.code, 'gone');
});

test('чужой игрок не может менять чужую комнату', async () => {
  const e = env();
  await post(e, '/api/register', { name: 'Host_1', token: TOKEN_A });
  await post(e, '/api/register', { name: 'Other_2', token: TOKEN_B });
  const open = await post(e, '/api/room', { name: 'Host_1', token: TOKEN_A, action: 'open', info: {} });
  const r = await post(e, '/api/room', { name: 'Other_2', token: TOKEN_B, action: 'close', code: open.data.code });
  assert.equal(r.status, 200);
  assert.equal(r.data.gone, true);
  assert.equal((await get(e, '/api/room/' + open.data.code)).status, 200, 'комната не должна закрыться');
});

test('список модов в комнате очищается: плохой sha1 отбрасывается', async () => {
  const e = env();
  await post(e, '/api/register', { name: 'Host_1', token: TOKEN_A });
  const open = await post(e, '/api/room', {
    name: 'Host_1', token: TOKEN_A, action: 'open',
    info: { mods: [
      { id: 'sodium', name: 'Sodium', sha1: SHA, size: 100 },
      { id: 'BAD ID!', name: 'x', sha1: SHA, size: 1 },
      { id: 'nohash', name: 'y', sha1: 'not-a-hash', size: 1 },
    ] },
  });
  const seen = await get(e, '/api/room/' + open.data.code);
  assert.deepEqual(seen.data.info.mods.map((m) => m.id), ['sodium']);
});

test('неверный ключ не открывает комнату (403)', async () => {
  const e = env();
  await post(e, '/api/register', { name: 'Host_1', token: TOKEN_A });
  const r = await post(e, '/api/room', { name: 'Host_1', token: TOKEN_B, action: 'open', info: {} });
  assert.equal(r.status, 403);
});
