/**
 * Chibu Control Plane — shared client-side utilities.
 * Vanilla JS, no framework required.
 */

// ── Toast notifications ────────────────────────────────────────────────────

const TOAST_CLASSES = {
  success: 'bg-emerald-900/80 text-emerald-300 border-emerald-700',
  error:   'bg-rose-900/80 text-rose-300 border-rose-700',
  info:    'bg-violet-900/80 text-violet-300 border-violet-700',
  warn:    'bg-amber-900/80 text-amber-300 border-amber-700',
};

let _toastTimer;
function showToast(msg, type = 'info', ms = 3500) {
  const el = document.getElementById('toast');
  if (!el) return;
  el.className = `fixed bottom-6 right-6 z-50 px-4 py-3 rounded-xl text-xs font-medium shadow-lg border fade-in ${TOAST_CLASSES[type] ?? TOAST_CLASSES.info}`;
  el.textContent = msg;
  el.classList.remove('hidden');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.add('hidden'), ms);
}

// ── Clipboard ─────────────────────────────────────────────────────────────

function copyToken(token) {
  navigator.clipboard.writeText(token)
    .then(() => showToast('Token copied to clipboard', 'success'))
    .catch(() => showToast('Copy failed — check browser permissions', 'error'));
}

// ── Agent card actions (dashboard) ────────────────────────────────────────

async function startAgent(agentId, btn) {
  if (btn) btn.disabled = true;
  const r = await fetch(`/agents/${agentId}/start`, { method: 'POST' });
  const d = await r.json();
  if (r.ok) {
    showToast(`Starting… pid ${d.pid ?? '—'}`, 'success');
    setTimeout(() => location.reload(), 2500);
  } else {
    showToast(d.detail ?? 'Failed to start', 'error');
    if (btn) btn.disabled = false;
  }
}

async function stopAgent(agentId, btn) {
  if (btn) btn.disabled = true;
  const r = await fetch(`/agents/${agentId}/stop`, { method: 'POST' });
  if (r.ok) {
    showToast('Agent stopped', 'info');
    setTimeout(() => location.reload(), 500);
  } else {
    const d = await r.json();
    showToast(d.detail ?? 'Failed to stop', 'error');
    if (btn) btn.disabled = false;
  }
}

// ── Create agent modal ─────────────────────────────────────────────────────

function openCreateModal() {
  document.getElementById('create-modal')?.classList.remove('hidden');
}

function closeCreateModal(event) {
  const modal = document.getElementById('create-modal');
  if (!event || event.target === modal) modal?.classList.add('hidden');
}

async function submitCreate(event) {
  event.preventDefault();
  const form = event.target;
  const btn = document.getElementById('create-btn');
  const data = Object.fromEntries(new FormData(form));
  if (!data.grpc_port) delete data.grpc_port;
  else data.grpc_port = parseInt(data.grpc_port, 10);

  btn.disabled = true;
  btn.textContent = 'Creating…';

  const r = await fetch('/agents/', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  const result = await r.json();

  if (r.ok) {
    showToast(`Agent '${result.name}' created!`, 'success');
    closeCreateModal();
    setTimeout(() => location.reload(), 800);
  } else {
    showToast(result.detail ?? 'Creation failed', 'error');
    btn.disabled = false;
    btn.textContent = 'Create Agent';
  }
}

// ── Search / filter agents grid ────────────────────────────────────────────

function filterAgents() {
  const q = document.getElementById('agent-search')?.value.toLowerCase() ?? '';
  document.querySelectorAll('.agent-card').forEach(card => {
    const name = card.dataset.name ?? '';
    const group = card.dataset.group ?? '';
    card.style.display = (name.includes(q) || group.includes(q)) ? '' : 'none';
  });
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────────

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeCreateModal();
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    document.getElementById('agent-search')?.focus();
  }
});
