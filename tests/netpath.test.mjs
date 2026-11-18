// Выбор пути к другу по задержке (web/js/net.js): pickFastest и netSwitchAdvice. Запуск: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const code = readFileSync(new URL('../web/js/net.js', import.meta.url), 'utf8');
const ctx = { onEvent: () => {}, setInterval, clearInterval, setTimeout, S: { state: 'idle' } };
vm.createContext(ctx);
vm.runInContext(`${code}\nthis.pickFastest = pickFastest; this.netSwitchAdvice = netSwitchAdvice; this.measurePaths = measurePaths;`, ctx);
const { pickFastest, netSwitchAdvice, measurePaths } = ctx;

test('быстрый путь выигрывает', () => {
  assert.equal(pickFastest([{ kind: 'relay', ms: 120 }, { kind: 'e4mc', ms: 40 }]).kind, 'e4mc');
});

test('при равенстве (разница до 20 мс) — прежний порядок', () => {
  assert.equal(pickFastest([{ kind: 'relay', ms: 60 }, { kind: 'e4mc', ms: 45 }]).kind, 'relay');
  assert.equal(pickFastest([{ kind: 'relay', ms: 60 }, { kind: 'e4mc', ms: 60 }]).kind, 'relay');
});

test('не ответивший путь пропускается', () => {
  assert.equal(pickFastest([{ kind: 'relay', ms: null }, { kind: 'e4mc', ms: 300 }]).kind, 'e4mc');
  assert.equal(pickFastest([{ kind: 'relay', ms: null }, { kind: 'e4mc', ms: null }]), null);
});

const series = (now, from, every, ms) => {
  const out = [];
  for (let t = now - from; t <= now; t += every) out.push({ t, ms });
  return out;
};

test('предлагаем другой путь, если текущий хуже на 100+ мс целую минуту', () => {
  const now = 1e9;
  const hist = { relay: series(now, 60000, 5000, 260), e4mc: series(now, 60000, 20000, 90) };
  const a = netSwitchAdvice('relay', hist, ['e4mc'], now - 70000, now);
  assert.equal(a.kind, 'e4mc');
  assert.equal(a.cur, 260);
  assert.equal(a.alt, 90);
});

test('меньше минуты слежения — не предлагаем', () => {
  const now = 1e9;
  const hist = { relay: series(now, 40000, 5000, 260), e4mc: series(now, 40000, 20000, 90) };
  assert.equal(netSwitchAdvice('relay', hist, ['e4mc'], now - 40000, now), null);
});

test('разница меньше 100 мс — не предлагаем', () => {
  const now = 1e9;
  const hist = { relay: series(now, 60000, 5000, 150), e4mc: series(now, 60000, 20000, 70) };
  assert.equal(netSwitchAdvice('relay', hist, ['e4mc'], now - 70000, now), null);
});

test('один скачок задержки не считается (медиана)', () => {
  const now = 1e9;
  const relay = series(now, 60000, 5000, 60);
  relay[3] = { ...relay[3], ms: 900 };
  relay[7] = { ...relay[7], ms: 900 };
  const hist = { relay, e4mc: series(now, 60000, 20000, 40) };
  assert.equal(netSwitchAdvice('relay', hist, ['e4mc'], now - 70000, now), null);
});

test('против прямого соединения задержка ретранслятора считается вдвое', () => {
  const now = 1e9;
  const hist = { p2p: series(now, 60000, 5000, 200), relay: series(now, 60000, 20000, 60) };
  assert.equal(netSwitchAdvice('p2p', hist, ['relay'], now - 70000, now), null);   // 200 − 120 < 100
  const worse = { p2p: series(now, 60000, 5000, 260), relay: series(now, 60000, 20000, 60) };
  assert.equal(netSwitchAdvice('p2p', worse, ['relay'], now - 70000, now).alt, 120);
});

test('замер с таймаутом: зависший путь даёт null', async () => {
  const got = await measurePaths([async () => ({ ms: 33 }), () => new Promise(() => {}), async () => { throw new Error('x'); }], 50);
  assert.deepEqual([...got], [33, null, null]);
});
