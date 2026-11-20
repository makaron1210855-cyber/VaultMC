/* «Лишние моды»: что в сборке реально бесполезно или мешает (два рендера, два движка света, переходник без программы,
 * библиотека, которую никто не просит). Правила и причины — core/optaudit.py. Моды выключаются, а не удаляются:
 * включить обратно можно переключателем на вкладке «Моды», а перед чисткой лаунчер сохраняет точку в «Истории». */

const OA_KINDS = {
  twin: 'два мода одной роли',
  obsolete: 'уже не нужен',
  orphan: 'нечего подключать',
  conflict: 'мешает другому моду',
  dup: 'лежит дважды',
  library: 'никто не просит',
};

function openModAudit(inst, onDone) {
  const m = openModal(`<h3>Лишние моды в «${esc(inst.name)}»</h3>
    <p class="muted small">Лаунчер ищет только то, что точно бесполезно или мешает: два мода одной роли (например, два рендера
      или два движка света), устаревшие переходники, дубли, библиотеки без «клиентов». Моды, которые нужны другим модам,
      не предлагаются. Ничего не удаляется — мод выключается, и его можно включить обратно на вкладке «Моды».</p>
    <div id="oaBody"><div class="row muted"><span class="spinner"></span> Проверяю моды...</div></div>`, { wide: true });
  const body = $('#oaBody', m);

  const scan = async () => {
    body.innerHTML = '<div class="row muted"><span class="spinner"></span> Проверяю моды...</div>';
    const r = await safe(api.mod_audit(inst.id));
    if (!m.isConnected) return;
    if (!r || r.error) { body.innerHTML = `<p class="note">${esc(r?.error || 'Не удалось проверить моды')}</p>`; return; }
    if (!r.items.length) {
      body.innerHTML = `<div class="crash-item"><div class="grow"><b>${ic('check')} Лишнего не найдено</b>
        <p class="small muted">Проверено модов: ${r.mods}. Дублей, конфликтующих и бесполезных модов нет — набор в порядке, ничего убирать не нужно.</p></div></div>
        <div class="foot"><div class="grow"></div><button class="btn primary" data-close>Закрыть</button></div>`;
      hydrateIcons(body);
      body.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
      return;
    }
    body.innerHTML = `
      <p class="small muted">Проверено модов: ${r.mods}. Найдено лишнего: <b>${r.items.length}</b>. Снимите галочку с того, что хотите оставить.</p>
      <div class="list" style="max-height:min(400px,48vh);overflow:auto">${r.items.map((i) => `
        <div class="item" style="padding:8px 12px;align-items:flex-start">
          <label class="check grow" style="gap:12px;align-items:flex-start">
            <input type="checkbox" data-f="${esc(i.file)}" checked><span class="box">${iconSvg('check')}</span>
            <div class="grow"><div class="title">${esc(i.name)} <span class="chip warn">${esc(OA_KINDS[i.kind] || '')}</span></div>
              <div class="small faint">${esc(i.file)}</div>
              <div class="small muted" style="margin-top:4px;line-height:1.45">${esc(i.why)}</div></div></label></div>`).join('')}</div>
      <div class="foot"><button class="btn ghost" data-close>Ничего не трогать</button>
        <button class="btn primary" id="oaGo">${ic('trash')} Отключить выбранные (${r.items.length})</button></div>`;
    hydrateIcons(body);
    body.querySelectorAll('[data-close]').forEach((b) => (b.onclick = m.close));
    const boxes = () => [...body.querySelectorAll('input[data-f]')];
    const sync = () => {
      const n = boxes().filter((b) => b.checked).length;
      $('#oaGo', body).disabled = !n;
      $('#oaGo', body).lastChild.textContent = ` Отключить выбранные (${n})`;
    };
    body.addEventListener('change', sync);
    $('#oaGo', body).onclick = async () => {
      const files = boxes().filter((b) => b.checked).map((b) => b.dataset.f);
      $('#oaGo', body).disabled = true;
      const res = await safe(api.mod_audit_apply(inst.id, files));
      if (!m.isConnected) return;
      if (!res || res.error) { if (res?.error) toast(res.error, 'error'); return scan(); }
      m.close();
      toast(`Отключено модов: ${res.disabled.length}${res.blocked.length ? `; оставлено: ${res.blocked.length} (нужны другим модам)` : ''}. Включить обратно — на вкладке «Моды»`, res.blocked.length ? 'warn' : 'info', 9000);
      if (onDone) onDone();
    };
  };
  scan();
}
