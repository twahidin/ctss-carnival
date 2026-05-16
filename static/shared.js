// Tiny shared helpers: api(), toast(), confirmModal()
window.api = async function api(method, url, body) {
  const opts = { method, credentials: 'same-origin', headers: {} };
  if (body !== undefined) {
    if (body instanceof FormData) {
      opts.body = body;
    } else {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
  }
  const r = await fetch(url, opts);
  let data = null;
  try { data = await r.json(); } catch (_) {}
  if (!r.ok) {
    const msg = (data && (data.error || data.detail?.error || data.detail)) || `HTTP ${r.status}`;
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg));
  }
  return data;
};

window.toast = function toast(msg, kind = 'success') {
  const el = document.createElement('div');
  const color = { success: 'bg-green-600', error: 'bg-red-600', warn: 'bg-amber-600' }[kind];
  el.className = `${color} text-white px-4 py-2 rounded shadow-lg fixed top-4 left-1/2 -translate-x-1/2 z-50`;
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2000);
};

window.confirmModal = function confirmModal({ title, body, confirmLabel = 'Confirm', confirmKind = 'green' }) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'fixed inset-0 bg-black/50 flex items-center justify-center z-40';
    overlay.innerHTML = `
      <div class="bg-white rounded-lg shadow-xl p-6 max-w-sm w-11/12">
        <h3 class="text-lg font-bold mb-2">${title}</h3>
        <div class="mb-4">${body}</div>
        <div class="flex gap-2 justify-end">
          <button class="px-4 py-2 rounded bg-gray-200" data-act="cancel">Cancel</button>
          <button class="px-4 py-2 rounded bg-${confirmKind}-600 text-white" data-act="ok">${confirmLabel}</button>
        </div>
      </div>`;
    overlay.addEventListener('click', e => {
      if (e.target.dataset.act === 'ok') { overlay.remove(); resolve(true); }
      else if (e.target.dataset.act === 'cancel' || e.target === overlay) { overlay.remove(); resolve(false); }
    });
    document.body.appendChild(overlay);
  });
};
