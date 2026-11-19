'use strict';
/* UI only. Every permission rule is enforced by the server (app.py). */
const $ = id => document.getElementById(id);
function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (k in el) el[k] = v;
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat(Infinity)) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
}
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = s => { if (!s) return '—'; const [d, t] = s.split(' '); const dt = new Date(d + 'T00:00:00');
  return dt.toLocaleDateString('en-GB', {day:'2-digit', month:'short', year:'numeric'}) + (t ? ', ' + t.slice(0, 5) : ''); };
const mb = n => (n / 1048576).toFixed(2) + ' MB';
function toast(m) { const t = $('toast'); t.textContent = m; t.classList.add('show'); setTimeout(() => t.classList.remove('show'), 3000); }

let ME = null, CSRF = '', META = {}, UNITS = [], CUR = null, firstLoad = true, chosen = [], lastReport = null, ADMIN_UNITS = [];
const collapsed = new Set();
const isAdmin = () => ME && ME.role === 'admin';
const STATUS = () => META.status_labels || {};

async function api(path, {method = 'GET', json, form} = {}) {
  const opt = {method, headers: {}, credentials: 'same-origin'};
  if (CSRF) opt.headers['X-CSRF-Token'] = CSRF;
  if (json !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(json); }
  if (form) opt.body = form;
  const r = await fetch(path, opt);
  let data = {}; try { data = await r.json(); } catch (e) {}
  if (!r.ok) {
    if (r.status === 401 && path !== '/api/login' && path !== '/api/me') showLogin('Your session has ended. Please sign in again.');
    if (data.code === 'password_change_required') showPw();
    const e = new Error(data.error || 'Something went wrong. Please try again.'); e.status = r.status; throw e;
  }
  return data;
}

/* ------------------------------------------------------------ views / auth */
function show(view) { for (const v of ['loginView', 'pwView', 'appView']) $(v).hidden = v !== view; }
function showLogin(msg) { ME = null; CSRF = ''; show('loginView'); $('loginError').textContent = msg || ''; $('loginForm').reset(); }
function showPw() { show('pwView'); $('pwForm').reset(); $('pwError').textContent = ''; }

$('togglePassword').onclick = () => { const p = $('password'); p.type = p.type === 'password' ? 'text' : 'password'; $('togglePassword').textContent = p.type === 'password' ? 'Show' : 'Hide'; };
$('loginForm').addEventListener('submit', async e => {
  e.preventDefault(); $('loginError').textContent = '';
  try { await api('/api/login', {method: 'POST', json: {email: $('email').value, password: $('password').value}}); await boot(); }
  catch (er) { $('loginError').textContent = er.message; }
});
$('logoutBtn').onclick = async () => { try { await api('/api/logout', {method: 'POST'}); } catch (e) {} showLogin(); };
$('pwBtn').onclick = showPw;
$('pwForm').addEventListener('submit', async e => {
  e.preventDefault();
  if ($('pwNew').value !== $('pwNew2').value) { $('pwError').textContent = 'The two new passwords do not match.'; return; }
  try { await api('/api/change-password', {method: 'POST', json: {current: $('pwCurrent').value, new: $('pwNew').value}});
    toast('Password updated.'); await boot(); }
  catch (er) { $('pwError').textContent = er.message; }
});

async function boot() {
  try { const m = await api('/api/me'); ME = m.user; CSRF = m.csrf; META = m; }
  catch (e) { showLogin(); return; }
  $('topYear').textContent = 'ACADEMIC YEAR ' + META.current_year.replace('-', '–');
  if (ME.must_change) { showPw(); return; }
  show('appView'); setupApp();
}

/* ------------------------------------------------------------- app set-up */
function setupApp() {
  $('welcomeTitle').textContent = 'Welcome, ' + ME.name;
  $('roleLabel').textContent = isAdmin() ? 'PRINCIPAL / ADMINISTRATOR WORKSPACE' : 'DEPARTMENT / SECTION WORKSPACE';
  $('welcomeSub').textContent = isAdmin() ? 'You can view every section, comment on documents and mark them approved or for revision.'
    : 'Upload your documents here. The Principal\'s remarks will appear on each document.';
  $('userEmail').textContent = ME.email;
  $('adminTabBtn').hidden = !isAdmin();
  $('noticeFormPanel').hidden = !isAdmin();
  $('noticeSub').textContent = isAdmin() ? 'Choose which sections can see each document. You can see who has opened it.' : 'Circulars and documents sent to your section by the Principal.';
  $('nYear').replaceChildren(...META.years.map(y => h('option', {value: y, text: y.replace('-', '–')}))); $('nYear').value = META.current_year; $('nDate').value = META.today;
  $('fNewLabel').textContent = isAdmin() ? 'Not yet opened' : 'New remarks';
  $('navHint').textContent = isAdmin() ? 'Red numbers = documents you have not opened yet.' : 'Red numbers = new remarks from the Principal.';
  const yrs = META.years;
  $('academicYear').replaceChildren(...yrs.map(y => h('option', {value: y, text: y.replace('-', '–')})));
  $('academicYear').value = META.current_year;
  $('fYear').replaceChildren(h('option', {value: '', text: 'Any year'}), ...yrs.map(y => h('option', {value: y, text: y.replace('-', '–')})));
  $('recordType').replaceChildren(h('option', {value: '', text: 'Choose type'}), ...META.doc_types.map(t => h('option', {value: t, text: t})));
  $('rFrom').value = $('rTo').value = META.today; $('recordDate').value = META.today;
  $('submittedBy').value = '';
  switchTab('docs'); firstLoad = true; CUR = null; resetFilters(false);
  refreshAll();
}
async function refreshAll() {
  await loadUnits();
  if (firstLoad) {
    firstLoad = false;
    const roots = UNITS.filter(u => u.accessible && !(UNITS.find(p => p.id === u.parent_id) || {}).accessible);
    if (!isAdmin() && roots.length === 1) CUR = roots[0].id;
    if (isAdmin()) { UNITS.filter(u => u.has_children).forEach(u => collapsed.add(u.id)); renderNav(); }
  }
  selectUnit(CUR, true); loadStats();
}
setInterval(() => { if (ME && !document.hidden && $('modal').hidden && !$('appView').hidden) { loadUnits(); loadStats(); if (!$('tab-docs').hidden) loadDocs(); } }, 60000);

function switchTab(t) {
  document.querySelectorAll('#mainTabs button').forEach(b => b.classList.toggle('active', b.dataset.tab === t));
  for (const x of ['docs', 'notices', 'reports', 'admin']) $('tab-' + x).hidden = x !== t;
  if (t === 'notices') loadNotices();
  if (t === 'reports') fillReportUnits();
  if (t === 'admin') loadAdmin(document.querySelector('#adminTabs .active').dataset.sub);
}
$('mainTabs').onclick = e => { if (e.target.dataset.tab) switchTab(e.target.dataset.tab); };

async function loadStats() {
  refreshNoticeBadge();
  try {
    const s = await api('/api/stats');
    const cards = isAdmin()
      ? [['TOTAL DOCUMENTS', s.total], ['NOT YET OPENED', s.new], ['CHANGES REQUESTED', s.revision], ['UPLOADED TODAY', s.today]]
      : [['MY DOCUMENTS', s.total], ['NEW REMARKS', s.new], ['CHANGES REQUESTED', s.revision], ['UPLOADED TODAY', s.today]];
    $('stats').replaceChildren(...cards.map(([l, v]) => h('div', {class: 'stat panel'}, h('span', {text: l}), h('strong', {text: v}))));
  } catch (e) {}
}

/* ------------------------------------------------------------- navigation */
async function loadUnits() { UNITS = (await api('/api/units')).units; renderNav(); }
const unitById = id => UNITS.find(u => u.id === id);
function pathOf(id) { const p = []; let u = unitById(id); while (u) { p.push(u.name); u = unitById(u.parent_id); } return p.reverse().join(' › '); }
function depthOf(u) { let d = 0; while ((u = unitById(u.parent_id))) d++; return d; }

function renderNav() {
  const nav = $('sectionNav'); nav.replaceChildren();
  nav.append(h('div', {class: 'nav-row'}, h('span', {class: 'caret'}),
    h('button', {class: 'nav-item' + (CUR === null ? ' active' : ''), onclick: () => selectUnit(null)}, h('span', {class: 'lbl', text: 'All my sections'}))));
  const kids = {};
  UNITS.forEach(u => (kids[u.parent_id ?? 0] = kids[u.parent_id ?? 0] || []).push(u));
  const build = pid => (kids[pid] || []).map(u => {
    const label = (u.code ? u.code + '  ' : '') + u.name + (u.active ? '' : ' (inactive)');
    const caret = u.has_children
      ? h('button', {class: 'caret', title: 'Expand / collapse', onclick: () => { collapsed.has(u.id) ? collapsed.delete(u.id) : collapsed.add(u.id); renderNav(); }}, collapsed.has(u.id) ? '▸' : '▾')
      : h('span', {class: 'caret'});
    const main = u.accessible
      ? h('button', {class: 'nav-item' + (CUR === u.id ? ' active' : ''), onclick: () => selectUnit(u.id)},
          h('span', {class: 'lbl', text: label}),
          h('span', {}, u.badge ? h('span', {class: 'badge', text: u.badge}) : null, u.doc_count ? h('span', {class: 'cnt', text: ' ' + u.doc_count}) : null))
      : h('div', {class: 'nav-group-label', text: label});
    return h('div', {}, h('div', {class: 'nav-row'}, caret, main),
      u.has_children && !collapsed.has(u.id) ? h('div', {class: 'nav-children'}, build(u.id)) : null);
  });
  nav.append(...build(0));
}

function selectUnit(id, keepFilters) {
  CUR = id; renderNav();
  const u = id ? unitById(id) : null;
  $('sectionTitle').textContent = u ? u.name : 'All documents';
  $('sectionPath').textContent = u ? pathOf(id).toUpperCase() : 'ALL SECTIONS';
  $('sectionDescription').textContent = !u ? (isAdmin() ? 'Documents from every section, newest first.' : 'Documents from all sections assigned to you.')
    : u.has_children ? 'Includes everything uploaded under this heading.' + (isAdmin() ? '' : ' Choose a specific section on the left to upload.') : '';
  const up = !!(u && u.uploadable);
  $('uploadPanel').hidden = !up;
  if (up) { $('uploadTarget').textContent = pathOf(id); $('quarterWrap').hidden = !u.quarterly; $('quarter').required = u.quarterly;
    $('dropHint').textContent = `Images, PDF, Word, Excel, PowerPoint · up to ${META.max_file_mb} MB per file`; }
  loadDocs();
}

/* --------------------------------------------------------------- documents */
function filterParams() {
  const p = new URLSearchParams();
  if (CUR) p.set('unit_id', CUR);
  if ($('fYear').value) p.set('year', $('fYear').value);
  if ($('fStatus').value) p.set('status', $('fStatus').value);
  if ($('fSearch').value.trim()) p.set('q', $('fSearch').value.trim());
  if ($('fDate').value) { p.set('from', $('fDate').value); p.set('to', $('fDate').value); }
  if ($('fNew').checked) p.set('new', '1');
  return p;
}
function resetFilters(reload = true) { $('fYear').value = ''; $('fStatus').value = ''; $('fSearch').value = ''; $('fDate').value = ''; $('fNew').checked = false; if (reload) loadDocs(); }
let searchTimer;
$('fSearch').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(loadDocs, 300); });
['fYear', 'fStatus', 'fDate', 'fNew'].forEach(i => $(i).addEventListener('change', loadDocs));
$('clearFilter').onclick = () => resetFilters();

async function loadDocs() {
  const list = $('recordsList');
  try {
    const {documents} = await api('/api/documents?' + filterParams());
    if (!documents.length) { list.replaceChildren(h('div', {class: 'empty', text: 'No documents found.'})); return; }
    const showPath = !CUR || (unitById(CUR) || {}).has_children;
    list.replaceChildren(...documents.map(d => h('div', {class: 'record', tabindex: 0, onclick: () => openDoc(d.id), onkeydown: e => { if (e.key === 'Enter') openDoc(d.id); }},
      h('div', {class: 'record-icon', text: d.file_names && /\.(jpe?g|png|gif|webp)(;|$)/i.test(d.file_names) ? 'IMG' : 'DOC'}),
      h('div', {class: 'record-main'},
        h('div', {class: 'tag-row'}, h('span', {class: 'pill ' + d.status, text: STATUS()[d.status]}),
          d.is_new ? h('span', {class: 'pill new', text: isAdmin() ? 'NEW' : 'NEW REMARK'}) : null),
        h('strong', {text: d.title}),
        showPath ? h('small', {class: 'path', text: d.unit_path}) : null,
        h('small', {text: `${d.doc_date} · ${d.academic_year.replace('-', '–')}${d.quarter ? ' · ' + d.quarter : ''} · ${d.doc_type} · ${d.file_count} file(s) · ${d.comment_count} comment(s)`}),
        h('small', {text: `Uploaded ${fmt(d.created_at)} by ${d.submitted_by || d.owner_name}`})))));
  } catch (e) { list.replaceChildren(h('div', {class: 'empty', text: e.message})); }
}

/* ------------------------------------------------------------------ upload */
$('files').addEventListener('change', e => {
  const max = META.max_file_mb * 1048576, inc = [...e.target.files];
  const ok = inc.filter(f => f.size <= max);
  if (ok.length !== inc.length) toast(`Files above ${META.max_file_mb} MB were skipped.`);
  chosen = [...chosen, ...ok].slice(0, 10); renderChosen(); e.target.value = '';
});
function renderChosen() {
  $('selectedFiles').replaceChildren(...chosen.map((f, i) => h('div', {}, `${f.name} · ${mb(f.size)} `,
    h('button', {type: 'button', class: 'btn soft', onclick: () => { chosen.splice(i, 1); renderChosen(); }}, 'Remove'))));
}
function clearUploadForm() {
  chosen = []; renderChosen(); $('uploadForm').reset(); $('academicYear').value = META.current_year; $('recordDate').value = META.today; $('uploadError').textContent = '';
}
$('clearUpload').onclick = clearUploadForm;
$('uploadForm').addEventListener('submit', async e => {
  e.preventDefault(); $('uploadError').textContent = '';
  if (!chosen.length) { $('uploadError').textContent = 'Attach at least one file.'; return; }
  const fd = new FormData();
  fd.append('unit_id', CUR); fd.append('academic_year', $('academicYear').value); fd.append('quarter', $('quarter').value);
  fd.append('doc_date', $('recordDate').value); fd.append('title', $('recordTitle').value); fd.append('doc_type', $('recordType').value);
  fd.append('submitted_by', $('submittedBy').value); fd.append('notes', $('recordNotes').value);
  chosen.forEach(f => fd.append('files', f));
  $('uploadBtn').disabled = true; $('uploadBtn').textContent = 'Uploading…';
  try { await api('/api/documents', {method: 'POST', form: fd}); clearUploadForm(); toast('Document submitted to the Principal.'); await loadUnits(); loadDocs(); loadStats(); }
  catch (er) { $('uploadError').textContent = er.message; }
  finally { $('uploadBtn').disabled = false; $('uploadBtn').textContent = 'Submit document'; }
});

/* ------------------------------------------------------------ document modal */
$('modalClose').onclick = closeModal;
$('modal').addEventListener('mousedown', e => { if (e.target === $('modal')) closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('modal').hidden) closeModal(); });
function closeModal() { $('modal').hidden = true; $('modalBody').replaceChildren(); loadUnits(); loadStats(); loadDocs(); if (!$('tab-notices').hidden) loadNotices(); }

async function openDoc(id) {
  try { const {document: d} = await api('/api/documents/' + id); renderDoc(d); $('modal').hidden = false; }
  catch (e) { toast(e.message); }
}
async function act(fn, okMsg, id) {
  try { await fn(); if (okMsg) toast(okMsg); if (id) { const {document: d} = await api('/api/documents/' + id); renderDoc(d); } }
  catch (e) { toast(e.message); }
}
function renderDoc(d) {
  const L = STATUS();
  const meta = [['Section', d.unit_path], ['Academic year', d.academic_year.replace('-', '–') + (d.quarter ? ' · ' + d.quarter : '')],
    ['Document date', d.doc_date], ['Record type', d.doc_type], ['Submitted by', d.submitted_by || '—'],
    ['Uploaded by', `${d.owner_name} · ${fmt(d.created_at)}`], ['Description', d.notes || '—']];
  const thread = d.comments.length ? d.comments.map(c => {
    if (c.kind === 'status') return h('div', {class: 'msg status' + (c.unseen ? ' unseen' : '')}, h('div', {class: 'who', text: c.author}),
      h('p', {}, c.is_admin ? `Marked as “${L[c.new_status]}”.` : '', c.body ? (c.is_admin ? ' ' : '') + c.body : ''), h('span', {class: 'when', text: fmt(c.created_at)}));
    return h('div', {class: 'msg' + (c.is_admin ? ' admin' : '') + (c.unseen ? ' unseen' : '')},
      h('div', {class: 'who'}, c.author, h('span', {class: 'when', text: fmt(c.created_at)})), h('p', {text: c.body}));
  }) : [h('p', {class: 'muted small', text: 'No comments yet.'})];
  const cbox = h('textarea', {rows: 3, maxLength: 2000, placeholder: isAdmin() ? 'Write a remark for the department…' : 'Reply to the Principal…'});
  const note = h('textarea', {rows: 2, maxLength: 2000, placeholder: 'Comment for the department (required when requesting changes)'});
  const setStatus = st => act(() => api(`/api/documents/${d.id}/status`, {method: 'POST', json: {status: st, note: note.value}}), 'Status updated.', d.id);
  const fileIn = h('input', {type: 'file', multiple: true, accept: 'image/*,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.csv'});
  const el = h('div', {},
    h('div', {class: 'tag-row'}, h('span', {class: 'pill ' + d.status, text: L[d.status]}), d.is_new ? h('span', {class: 'pill new', text: 'NEW'}) : null),
    h('h3', {text: d.title}),
    h('dl', {class: 'meta-grid'}, meta.map(([k, v]) => [h('dt', {text: k}), h('dd', {text: v})])),
    h('h4', {text: 'Files'}),
    d.files.map(f => h('div', {class: 'file-row'}, h('span', {text: `${f.orig_name} · ${mb(f.size)}`}),
      h('span', {}, f.previewable ? h('a', {href: `/api/files/${f.id}?view=1`, target: '_blank', rel: 'noopener', text: 'View'}) : null,
        h('a', {href: `/api/files/${f.id}`, text: 'Download'})))),
    d.can_add_files ? h('div', {class: 'review-box'}, h('b', {text: d.status === 'revision' ? 'Upload the revised file(s)' : 'Add another file'}), h('div', {}, fileIn),
      h('div', {class: 'form-actions'}, h('button', {class: 'btn primary', onclick: () => {
        if (!fileIn.files.length) { toast('Choose a file first.'); return; }
        const fd = new FormData(); [...fileIn.files].forEach(f => fd.append('files', f));
        act(() => api(`/api/documents/${d.id}/files`, {method: 'POST', form: fd}), 'File(s) uploaded.', d.id); }}, 'Upload'))) : null,
    h('h4', {text: 'Comments & review history'}), h('div', {class: 'thread'}, thread),
    cbox, h('div', {class: 'form-actions'}, h('button', {class: 'btn primary', onclick: () => {
      if (!cbox.value.trim()) { toast('Write a comment first.'); return; }
      act(() => api(`/api/documents/${d.id}/comments`, {method: 'POST', json: {body: cbox.value}}), 'Comment posted.', d.id); }}, isAdmin() ? 'Post remark' : 'Post reply')),
    isAdmin() ? h('div', {class: 'review-box'}, h('b', {text: 'Review decision'}), note,
      h('div', {class: 'form-actions'}, h('button', {class: 'btn good', onclick: () => setStatus('approved')}, 'Approve'),
        h('button', {class: 'btn warn', onclick: () => setStatus('revision')}, 'Request changes'),
        h('button', {class: 'btn soft', onclick: () => setStatus('submitted')}, 'Mark pending'))) : null,
    d.can_delete ? h('div', {class: 'form-actions'}, h('button', {class: 'btn danger', onclick: () => {
      if (confirm(`Delete “${d.title}” and its files permanently?`)) act(async () => { await api('/api/documents/' + d.id, {method: 'DELETE'}); closeModal(); toast('Document deleted.'); }); }}, 'Delete document')) : null);
  $('modalBody').replaceChildren(el);
}

/* ------------------------------------------------------------------ reports */
function fillReportUnits() {
  const cur = $('rUnit').value;
  $('rUnit').replaceChildren(h('option', {value: '', text: 'All my sections'}),
    ...UNITS.filter(u => u.accessible).map(u => h('option', {value: u.id, text: '\u00A0\u00A0'.repeat(depthOf(u)) + (u.code ? u.code + ' ' : '') + u.name})));
  $('rUnit').value = cur;
}
$('genReport').onclick = async () => {
  const p = new URLSearchParams({basis: $('rBasis').value});
  if ($('rUnit').value) p.set('unit_id', $('rUnit').value);
  if ($('rFrom').value) p.set('from', $('rFrom').value);
  if ($('rTo').value) p.set('to', $('rTo').value);
  if ($('rStatus').value) p.set('status', $('rStatus').value);
  try {
    const {documents} = await api('/api/documents?' + p);
    lastReport = {rows: documents, scope: $('rUnit').value ? pathOf(Number($('rUnit').value)) : 'All sections',
      from: $('rFrom').value, to: $('rTo').value, basis: $('rBasis').options[$('rBasis').selectedIndex].text,
      by: `${ME.name} (${ME.email})`, at: new Date().toLocaleString('en-GB')};
    $('reportOut').hidden = false; renderReport();
  } catch (e) { toast(e.message); }
};
function reportBody(r) {
  const L = STATUS();
  const head = `<h2>Vivekananda College, Thakurpukur</h2><h3>Document Submission Report</h3><p><b>Section:</b> ${esc(r.scope)}<br><b>Period (${esc(r.basis)}):</b> ${esc(r.from || '…')} to ${esc(r.to || '…')}<br><b>Generated by:</b> ${esc(r.by)} on ${esc(r.at)}<br><b>Total:</b> ${r.rows.length} document(s), ${r.rows.reduce((n, d) => n + d.file_count, 0)} file(s)</p>`;
  const rows = r.rows.map((d, i) => `<tr><td>${i + 1}</td><td>${esc(d.unit_path)}</td><td>${esc(d.title)}<br><small>${esc(d.doc_type)}</small></td><td>${esc(d.academic_year)}${d.quarter ? ' ' + esc(d.quarter) : ''}</td><td>${esc(d.doc_date)}<br><small>Uploaded ${esc(fmt(d.created_at))}</small></td><td>${esc(d.submitted_by || d.owner_name)}</td><td>${esc(L[d.status])}</td><td>${esc(d.file_names || '')}</td></tr>`).join('');
  return head + `<div class="table-wrap"><table class="data"><thead><tr><th>#</th><th>Section</th><th>Title / Type</th><th>Year</th><th>Dates</th><th>Submitted by</th><th>Status</th><th>Files</th></tr></thead><tbody>${rows || '<tr><td colspan="8">No documents in this period.</td></tr>'}</tbody></table></div>`;
}
function renderReport() { $('reportPrint').innerHTML = reportBody(lastReport); }  // every value passed through esc()
function download(blob, name) { const a = h('a', {href: URL.createObjectURL(blob), download: name}); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); }
$('dlHtml').onclick = () => lastReport && download(new Blob([`<!doctype html><html><head><meta charset="utf-8"><title>DOC Report</title><style>body{font:14px Arial;margin:35px;color:#17344b}table{border-collapse:collapse;width:100%}td,th{border:1px solid #aaa;padding:8px;text-align:left;vertical-align:top}th{background:#eef4f6}</style></head><body>${reportBody(lastReport)}<p style="margin-top:45px">_________________________<br>Authorised Signatory</p></body></html>`], {type: 'text/html'}), `DOC_Report_${new Date().toISOString().slice(0, 10)}.html`);
$('dlCsv').onclick = () => {
  if (!lastReport) return; const L = STATUS();
  const rows = [['Section', 'Title', 'Record type', 'Academic year', 'Quarter', 'Document date', 'Uploaded at', 'Submitted by', 'Uploaded by', 'Status', 'Files', 'Remarks'],
    ...lastReport.rows.map(d => [d.unit_path, d.title, d.doc_type, d.academic_year, d.quarter || '', d.doc_date, d.created_at, d.submitted_by, d.owner_name, L[d.status], d.file_names || '', d.notes])];
  // prefix ' to values starting with = + - @ so spreadsheets never run them as formulas
  const cell = v => { v = String(v ?? ''); if (/^[=+\-@]/.test(v)) v = "'" + v; return '"' + v.replace(/"/g, '""') + '"'; };
  download(new Blob(['\ufeff' + rows.map(r => r.map(cell).join(',')).join('\r\n')], {type: 'text/csv;charset=utf-8'}), `DOC_Report_${new Date().toISOString().slice(0, 10)}.csv`);
};
$('printReport').onclick = () => window.print();

/* -------------------------------------------------------------------- admin */
$('adminTabs').onclick = e => { if (!e.target.dataset.sub) return;
  document.querySelectorAll('#adminTabs button').forEach(b => b.classList.toggle('active', b === e.target)); loadAdmin(e.target.dataset.sub); };
async function loadAdmin(sub) {
  for (const s of ['users', 'units', 'audit', 'backup']) $('sub-' + s).hidden = s !== sub;
  try {
    ADMIN_UNITS = (await api('/api/admin/units')).units;
    if (sub === 'users') await adminUsers(); else if (sub === 'units') adminUnits(); else if (sub === 'audit') await adminAudit();
  } catch (e) { toast(e.message); }
}
function secret(pw, who) {
  return h('div', {class: 'secret-box'}, `Temporary password for ${who}: `, h('code', {text: pw}),
    h('div', {class: 'small muted', text: 'Shown only once. Share it privately; the user must change it at first sign-in.'}));
}
async function adminUsers(note, editing) {
  const {users} = await api('/api/admin/users');
  const box = $('sub-users');
  const unitNames = ids => ids.map(i => (ADMIN_UNITS.find(u => u.id === i) || {}).name).filter(Boolean).join(', ') || '—';
  const table = h('div', {class: 'table-wrap'}, h('table', {class: 'data'},
    h('thead', {}, h('tr', {}, ['Name', 'Email', 'Role', 'Sections', 'Status', 'Last sign-in', ''].map(t => h('th', {text: t})))),
    h('tbody', {}, users.map(u => h('tr', {class: u.active ? '' : 'inactive'},
      h('td', {text: u.name}), h('td', {text: u.email}), h('td', {text: u.role === 'admin' ? 'Principal / Admin' : 'Member'}),
      h('td', {text: u.role === 'admin' ? 'All' : unitNames(u.unit_ids)}), h('td', {text: u.active ? (u.must_change ? 'Active · password not yet changed' : 'Active') : 'Disabled'}),
      h('td', {text: u.last_login ? fmt(u.last_login) : 'Never'}),
      h('td', {}, h('div', {class: 'row-actions'}, h('button', {class: 'btn soft', onclick: () => adminUsers(null, u)}, 'Edit'),
        h('button', {class: 'btn soft', onclick: async () => { if (!confirm(`Reset password for ${u.email}?`)) return;
          try { const r = await api(`/api/admin/users/${u.id}/reset-password`, {method: 'POST'}); adminUsers(secret(r.temp_password, u.email)); } catch (e) { toast(e.message); } }}, 'Reset password'))))))));
  box.replaceChildren(h('div', {class: 'section-head'}, h('div', {}, h('h3', {text: 'Users & access'}), h('p', {class: 'muted small', text: 'Create one login per department / section. Ticking a heading (e.g. Arts) gives access to everything under it.'})),
    h('button', {class: 'btn primary', onclick: () => adminUsers(null, {})}, '＋ Add user')), note, editing ? userForm(editing) : null, table);
}
function userForm(u) {
  const isNew = !u.id;
  const name = h('input', {value: u.name || '', maxLength: 100}), email = h('input', {type: 'email', value: u.email || '', disabled: !isNew});
  const role = h('select', {}, h('option', {value: 'member', text: 'Member (department / section)'}), h('option', {value: 'admin', text: 'Principal / Administrator'})); role.value = u.role || 'member';
  const active = h('input', {type: 'checkbox', checked: u.active !== 0 && u.active !== false});
  const sel = new Set(u.unit_ids || []);
  const pick = h('div', {class: 'unit-pick'}, ADMIN_UNITS.map(x => h('label', {}, h('input', {type: 'checkbox', checked: sel.has(x.id), onchange: e => e.target.checked ? sel.add(x.id) : sel.delete(x.id)}),
    '\u00A0\u00A0'.repeat(x.depth) + (x.code ? x.code + ' ' : '') + x.name)));
  const err = h('p', {class: 'error'});
  return h('div', {class: 'review-box'}, h('b', {text: isNew ? 'New user' : 'Edit user'}),
    h('label', {text: 'Name'}), name, h('label', {text: 'Email'}), email, h('label', {text: 'Role'}), role,
    h('label', {text: 'Sections this user can open and upload to'}), pick,
    isNew ? null : h('label', {class: 'check'}, active, ' Account active'), err,
    h('div', {class: 'form-actions'}, h('button', {class: 'btn primary', onclick: async () => {
      try {
        if (isNew) { const r = await api('/api/admin/users', {method: 'POST', json: {name: name.value, email: email.value, role: role.value, unit_ids: [...sel]}}); adminUsers(secret(r.temp_password, email.value)); }
        else { await api('/api/admin/users/' + u.id, {method: 'PATCH', json: {name: name.value, role: role.value, unit_ids: [...sel], active: active.checked}}); toast('User updated.'); adminUsers(); }
      } catch (e) { err.textContent = e.message; } }}, 'Save'), h('button', {class: 'btn soft', onclick: () => adminUsers()}, 'Cancel')));
}
function adminUnits() {
  const box = $('sub-units');
  const parent = h('select', {}, h('option', {value: '', text: '— Top level —'}), ADMIN_UNITS.map(x => h('option', {value: x.id, text: '\u00A0\u00A0'.repeat(x.depth) + (x.code ? x.code + ' ' : '') + x.name})));
  const code = h('input', {placeholder: 'e.g. 2.3.3', maxLength: 20}), name = h('input', {placeholder: 'e.g. Physics', maxLength: 120}), q = h('input', {type: 'checkbox'}); const err = h('p', {class: 'error'});
  const patch = async (x, body) => { try { await api('/api/admin/units/' + x.id, {method: 'PATCH', json: body}); await loadAdmin('units'); await loadUnits(); } catch (e) { toast(e.message); } };
  box.replaceChildren(h('h3', {text: 'Sections'}), h('p', {class: 'muted small', text: 'Add the missing Science departments here (the college document lists only Chemistry, Computer Science and Zoology, with “……” in between). Sections that already hold documents cannot receive sub-sections.'}),
    h('div', {class: 'review-box'}, h('b', {text: 'Add a section'}), h('label', {text: 'Parent'}), parent, h('label', {text: 'Number (optional, used for ordering)'}), code, h('label', {text: 'Name'}), name,
      h('label', {class: 'check'}, q, ' Needs a quarter (Q1–Q4) on every upload'), err,
      h('div', {class: 'form-actions'}, h('button', {class: 'btn primary', onclick: async () => { try {
        await api('/api/admin/units', {method: 'POST', json: {parent_id: parent.value || null, code: code.value, name: name.value, quarterly: q.checked}});
        toast('Section added.'); await loadAdmin('units'); await loadUnits(); } catch (e) { err.textContent = e.message; } }}, 'Add'))),
    h('div', {class: 'table-wrap'}, h('table', {class: 'data'}, h('thead', {}, h('tr', {}, ['Section', 'Number', 'Quarterly', 'Status', ''].map(t => h('th', {text: t})))),
      h('tbody', {}, ADMIN_UNITS.map(x => h('tr', {class: x.active ? '' : 'inactive'}, h('td', {text: '\u00A0\u00A0\u00A0'.repeat(x.depth) + x.name}), h('td', {text: x.code || '—'}),
        h('td', {text: x.quarterly ? 'Yes' : 'No'}), h('td', {text: x.active ? 'Active' : 'Inactive'}),
        h('td', {}, h('div', {class: 'row-actions'},
          h('button', {class: 'btn soft', onclick: () => { const n = prompt('Section name', x.name); if (n) patch(x, {name: n}); }}, 'Rename'),
          h('button', {class: 'btn soft', onclick: () => { const c = prompt('Number (e.g. 2.3.4)', x.code); if (c !== null) patch(x, {code: c}); }}, 'Number'),
          h('button', {class: 'btn soft', onclick: () => patch(x, {active: !x.active})}, x.active ? 'Deactivate' : 'Activate')))))))));
}
async function adminAudit() {
  const {entries} = await api('/api/admin/audit');
  $('sub-audit').replaceChildren(h('h3', {text: 'Activity log (latest 300)'}), h('div', {class: 'table-wrap'}, h('table', {class: 'data'},
    h('thead', {}, h('tr', {}, ['When (IST)', 'User', 'Action', 'Details', 'IP'].map(t => h('th', {text: t})))),
    h('tbody', {}, entries.map(e => h('tr', {}, h('td', {text: e.created_at}), h('td', {text: e.user_email || '—'}), h('td', {text: e.action}), h('td', {text: e.detail || ''}), h('td', {text: e.ip || ''})))))));
}

/* ------------------------------------------------- Principal's documents */
let nChosen = [];
async function refreshNoticeBadge() {
  if (isAdmin()) return;
  try { const {unread} = await api('/api/notices?count=1'); $('noticeBadge').hidden = !unread; $('noticeBadge').textContent = unread; } catch (e) {}
}
async function loadNotices() {
  try {
    const {notices} = await api('/api/notices'); refreshNoticeBadge();
    $('noticeList').replaceChildren(...(notices.length ? notices.map(n => h('div', {class: 'record', tabindex: 0, onclick: () => openNotice(n.id), onkeydown: e => { if (e.key === 'Enter') openNotice(n.id); }},
      h('div', {class: 'record-icon', text: 'DOC'}),
      h('div', {class: 'record-main'}, h('div', {class: 'tag-row'}, n.unread ? h('span', {class: 'pill new', text: 'NEW'}) : null),
        h('strong', {text: n.title}),
        h('small', {class: 'path', text: 'Visible to: ' + n.audience}),
        h('small', {text: `${n.doc_date} · ${n.academic_year.replace('-', '–')} · ${n.files.length} file(s) · issued ${fmt(n.created_at)}`})))) : [h('div', {class: 'empty', text: 'No documents yet.'})]));
  } catch (e) { toast(e.message); }
}
async function openNotice(id) {
  try { const {notice: n} = await api('/api/notices/' + id); renderNotice(n); $('modal').hidden = false; } catch (e) { toast(e.message); }
}
function renderNotice(n) {
  const rec = n.recipients;
  $('modalBody').replaceChildren(h('div', {},
    h('div', {class: 'tag-row'}, h('span', {class: 'pill approved', text: 'FROM THE PRINCIPAL'})),
    h('h3', {text: n.title}),
    h('dl', {class: 'meta-grid'}, [['Date', n.doc_date], ['Academic year', n.academic_year.replace('-', '–')], ['Visible to', n.audience], ['Issued by', `${n.author} · ${fmt(n.created_at)}`], ['Message', n.notes || '—']].map(([k, v]) => [h('dt', {text: k}), h('dd', {text: v})])),
    h('h4', {text: 'Files'}),
    n.files.map(f => h('div', {class: 'file-row'}, h('span', {text: `${f.orig_name} · ${mb(f.size)}`}),
      h('span', {}, f.previewable ? h('a', {href: `/api/notice-files/${f.id}?view=1`, target: '_blank', rel: 'noopener', text: 'View'}) : null, h('a', {href: `/api/notice-files/${f.id}`, text: 'Download'})))),
    rec ? h('div', {}, h('h4', {text: `Opened by ${rec.filter(r => r.read_at).length} of ${rec.length} account(s)`}),
      h('div', {class: 'table-wrap'}, h('table', {class: 'data'}, h('thead', {}, h('tr', {}, ['Account', 'Email', 'Opened'].map(t => h('th', {text: t})))),
        h('tbody', {}, rec.map(r => h('tr', {}, h('td', {text: r.name}), h('td', {text: r.email}), h('td', {text: r.read_at ? fmt(r.read_at) : 'Not yet'}))))))) : null,
    isAdmin() ? h('div', {class: 'form-actions'}, h('button', {class: 'btn danger', onclick: async () => {
      if (!confirm(`Delete “${n.title}” permanently? Departments will no longer see it.`)) return;
      try { await api('/api/notices/' + n.id, {method: 'DELETE'}); closeModal(); toast('Document deleted.'); } catch (e) { toast(e.message); } }}, 'Delete')) : null));
}
function buildUnitPick() {
  const sel = new Set(); const box = $('nUnits');
  box.replaceChildren(...UNITS.filter(u => u.active).map(u => h('label', {}, h('input', {type: 'checkbox', value: u.id, onchange: e => e.target.checked ? sel.add(u.id) : sel.delete(u.id)}),
    '\u00A0\u00A0'.repeat(depthOf(u)) + (u.code ? u.code + ' ' : '') + u.name)));
  box._sel = sel;
}
document.querySelectorAll('input[name=aud]').forEach(r => r.addEventListener('change', () => {
  const sel = document.querySelector('input[name=aud]:checked').value === 'selected';
  if (sel && !$('nUnits').childElementCount) buildUnitPick();
  $('nUnits').hidden = !sel; $('nUnitsHint').hidden = !sel; }));
$('nFiles').addEventListener('change', e => {
  const max = META.max_file_mb * 1048576, ok = [...e.target.files].filter(f => f.size <= max);
  if (ok.length !== e.target.files.length) toast(`Files above ${META.max_file_mb} MB were skipped.`);
  nChosen = [...nChosen, ...ok].slice(0, 10); renderN(); e.target.value = '';
});
function renderN() { $('nSelected').replaceChildren(...nChosen.map((f, i) => h('div', {}, `${f.name} · ${mb(f.size)} `, h('button', {type: 'button', class: 'btn soft', onclick: () => { nChosen.splice(i, 1); renderN(); }}, 'Remove')))); }
function clearNotice() { nChosen = []; renderN(); $('noticeForm').reset(); $('nYear').value = META.current_year; $('nDate').value = META.today; $('nUnits').hidden = $('nUnitsHint').hidden = true; $('nUnits').replaceChildren(); $('nError').textContent = ''; }
$('nClear').onclick = clearNotice;
$('noticeForm').addEventListener('submit', async e => {
  e.preventDefault(); $('nError').textContent = '';
  const aud = document.querySelector('input[name=aud]:checked').value, ids = aud === 'selected' ? [...($('nUnits')._sel || [])] : [];
  if (aud === 'selected' && !ids.length) { $('nError').textContent = 'Tick at least one section.'; return; }
  if (!nChosen.length) { $('nError').textContent = 'Attach at least one file.'; return; }
  const fd = new FormData(); fd.append('title', $('nTitle').value); fd.append('doc_date', $('nDate').value); fd.append('academic_year', $('nYear').value);
  fd.append('notes', $('nNotes').value); fd.append('audience', aud); ids.forEach(i => fd.append('unit_ids', i)); nChosen.forEach(f => fd.append('files', f));
  $('nBtn').disabled = true; $('nBtn').textContent = 'Uploading…';
  try { await api('/api/notices', {method: 'POST', form: fd}); clearNotice(); toast('Document issued.'); loadNotices(); }
  catch (er) { $('nError').textContent = er.message; }
  finally { $('nBtn').disabled = false; $('nBtn').textContent = 'Issue document'; }
});

/* logo fallback: show "VC" text if logo.png is missing */
(() => { const img = document.querySelector('.crest img'); const fb = () => { img.replaceWith(document.createTextNode('VC')); document.querySelector('.crest').style.fontSize = '22px'; };
  img.addEventListener('error', fb); if (img.complete && img.naturalWidth === 0) fb(); })();

boot();