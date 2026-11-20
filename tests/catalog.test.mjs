import test from 'node:test';
import assert from 'node:assert/strict';
import worker from '../server/worker.js';

const kv = (store) => ({ get: async (k) => (store[k] === undefined ? null : JSON.parse(store[k])) });
const get = (env) => worker.fetch(new Request('https://x/api/catalog'), env);

test('каталог: пусто, если записей нет', async () => {
  const r = await get({ SKINS: kv({}) });
  const d = await r.json();
  assert.equal(r.headers.get('access-control-allow-origin'), '*');
  assert.deepEqual(d.items, []);
});

test('каталог: отдаёт свежие, прячет устаревшие и с плохим кодом', async () => {
  const now = Date.now();
  const store = { catalog: JSON.stringify([
    { code: 'ABC123', name: 'Свежая', at: now - 1000 },
    { code: 'OLD111', name: 'Старая', at: now - 31 * 86400 * 1000 },
    { code: 'bad', name: 'Плохой код', at: now },
  ]) };
  const d = await (await get({ SKINS: kv(store) })).json();
  assert.deepEqual(d.items.map((i) => i.code), ['ABC123']);
});
