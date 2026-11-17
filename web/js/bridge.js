/* =============================================================================
   Мост к Python.  Вызов:  const result = await api.list_instances();
   В приложении (pywebview) — прямой вызов Python. В режиме разработки
   (python main.py --dev, обычный браузер) — HTTP-запрос к devserver.
   События из Python приходят в onEvent(name, handler).
   ============================================================================= */

const handlers = {};
function onEvent(name, fn) { (handlers[name] = handlers[name] || []).push(fn); }

window.__vaultEvents = (batch) => {
  for (const { e, p } of batch) (handlers[e] || []).forEach((fn) => { try { fn(p); } catch (err) { console.error(err); } });
};

let IN_APP = false;

async function callPython(name, args) {
  if (IN_APP) return window.pywebview.api[name](...args);
  const res = await fetch('/api/' + name, { method: 'POST', body: JSON.stringify(args) });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Ошибка');
  return data.result;
}

/** api.что_угодно(...) -> вызов метода Python с тем же именем */
const api = new Proxy({}, { get: (_, name) => (...args) => callPython(name, args) });

/** Ждём готовности моста (pywebview подгружается чуть позже страницы). */
function bridgeReady() {
  return new Promise((resolve) => {
    if (window.pywebview && window.pywebview.api) { IN_APP = true; return resolve(); }
    let done = false;
    window.addEventListener('pywebviewready', () => { if (!done) { done = true; IN_APP = true; resolve(); } });
    setTimeout(() => { // нет pywebview — значит, открыто в браузере (режим разработки)
      if (done) return;
      done = true;
      if (window.pywebview && window.pywebview.api) { IN_APP = true; return resolve(); }
      // опрос событий: часто, пока лаунчер на виду; редко во время игры и в свёрнутом окне
      const poll = async () => {
        try {
          const batch = await (await fetch('/events')).json();
          if (batch.length) window.__vaultEvents(batch);
        } catch (e) { /* сервер перезапускается */ }
        const slow = document.hidden || document.body.classList.contains('gaming');
        setTimeout(poll, slow ? 1000 : 150);
      };
      poll();
      resolve();
    }, 700);
  });
}
