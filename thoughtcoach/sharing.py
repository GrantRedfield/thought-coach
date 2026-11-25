"""Who can see your reviews: the owner's controls on their home page and on each review (the access itself is
enforced by the API, deploy/api/handler.py: these only ask it).

Shown only to the signed-in owner of the page (checked against /api/me). Everyone else never sees them.
"""

BUTTON = ("<button type='button' class='share-btn' data-share hidden aria-haspopup='dialog'>"
          "<svg viewBox='0 0 24 24' aria-hidden='true'><circle cx='18' cy='5' r='3'/><circle cx='6' cy='12' r='3'/><circle cx='18' cy='19' r='3'/>"
          "<path d='M8.6 10.5 15.4 6.5M8.6 13.5l6.8 4'/></svg><span>Share</span></button>")

PANEL = """<div class="share-panel" role="dialog" aria-label="Sharing" hidden>
  <div class="sp-head"><b>Who can see your reviews</b><button type="button" class="sp-close" aria-label="Close">&times;</button></div>
  <label class="sp-opt"><input type="radio" name="tc-vis" value="private"> <span><b>Private</b> (recommended): only you, and anyone you send a link to</span></label>
  <label class="sp-opt"><input type="radio" name="tc-vis" value="public"> <span><b>Public</b>: anyone who knows the address of your page</span></label>
  <div class="sp-new"><button type="button" class="sp-make" data-scope="game">Create a link to this game</button>
    <button type="button" class="sp-make" data-scope="all">Create a link to all your reviews</button></div>
  <div class="sp-made" hidden><input type="text" readonly aria-label="Share link"><button type="button" class="sp-copy">Copy</button></div>
  <p class="sp-k">Your links</p><ul class="sp-links"><li class="sp-none">None yet.</li></ul>
  <p class="sp-note">A link works until you revoke it. Revoking takes effect straight away, even for people who already opened it.</p>
</div>"""

CSS = """
.share-btn { display:inline-flex; align-items:center; gap:6px; min-height:40px; padding:0 14px; border-radius:10px; border:1px solid var(--line);
  background:var(--card); color:var(--fg); font:inherit; font-size:14px; font-weight:600; cursor:pointer; }
.share-btn svg { width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:2; }
.share-btn:hover { border-color:var(--accent); } .share-btn[hidden] { display:none; }
.share-panel { position:fixed; z-index:50; right:16px; top:64px; width:min(420px, calc(100vw - 32px)); max-height:calc(100vh - 90px); overflow:auto;
  background:var(--card); border:1px solid var(--line); border-radius:14px; padding:14px 16px; box-shadow:0 18px 50px #0008; font-size:14px; color:var(--fg2); }
.share-panel[hidden] { display:none; }
.sp-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; color:var(--fg); }
.sp-close { all:unset; cursor:pointer; font-size:22px; width:36px; height:36px; display:grid; place-items:center; }
.sp-opt { display:flex; gap:8px; align-items:flex-start; padding:8px 0; cursor:pointer; } .sp-opt b { color:var(--fg); }
.sp-new { display:flex; flex-wrap:wrap; gap:8px; margin:10px 0; }
.sp-make, .sp-copy, .sp-revoke { min-height:40px; padding:0 12px; border-radius:10px; border:1px solid var(--line); background:transparent; color:var(--fg);
  font:inherit; font-size:13.5px; font-weight:600; cursor:pointer; }
.sp-make:hover, .sp-copy:hover { border-color:var(--accent); } .sp-revoke:hover { border-color:#e03131; color:#ff8787; }
.sp-made { display:flex; gap:6px; margin:0 0 10px; } .sp-made input { flex:1; min-width:0; min-height:40px; padding:0 10px; border-radius:10px;
  border:1px solid var(--accent); background:var(--bg); color:var(--fg); font:inherit; font-size:13px; }
.sp-k { margin:6px 0 4px; font-size:12px; letter-spacing:.06em; text-transform:uppercase; color:var(--muted); font-weight:700; }
.sp-links { list-style:none; margin:0; padding:0; } .sp-links li { display:flex; align-items:center; gap:8px; padding:6px 0; border-top:1px solid var(--line); }
.sp-links li span { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:13px; }
.sp-note { margin:10px 0 0; font-size:12.5px; color:var(--muted); }
@media (max-width:640px) { .share-panel { top:auto; bottom:12px; right:12px; left:12px; width:auto; max-height:75vh; } }
"""

JS = """
(async () => {
  const m = /^\\/u\\/([a-z0-9_-]{2,30})\\/(?:games\\/([A-Za-z0-9]{8}))?/.exec(location.pathname);
  const btn = document.querySelector('[data-share]'), panel = document.querySelector('.share-panel');
  if (!m || !btn || !panel) return;
  const owner = m[1], game = m[2] || null;
  let me = {};
  try { me = await (await fetch('/api/me', { cache: 'no-store' })).json(); } catch (e) { return; }
  if (!me.signed_in || me.id !== owner) return;  // only the player sees these
  btn.hidden = false;
  if (!game) panel.querySelector('[data-scope=game]').remove();
  const list = panel.querySelector('.sp-links'), made = panel.querySelector('.sp-made'), input = made.querySelector('input');
  const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json());
  const full = (u) => location.origin + u;
  async function load() {
    const s = await (await fetch('/api/sharing', { cache: 'no-store' })).json();
    panel.querySelectorAll('input[name=tc-vis]').forEach(r => { r.checked = r.value === s.visibility; });
    list.textContent = '';
    if (!s.shares.length) { const li = document.createElement('li'); li.className = 'sp-none'; li.textContent = 'None yet.'; list.append(li); }
    for (const l of s.shares) {
      const li = document.createElement('li'), t = document.createElement('span'), rv = document.createElement('button');
      t.textContent = (l.game ? 'Game ' + l.game : 'All your reviews') + ' · ' + full(l.url); t.title = full(l.url);
      rv.type = 'button'; rv.className = 'sp-revoke'; rv.textContent = 'Revoke';
      rv.onclick = async () => { await post('/api/share/revoke', { token: l.token }); load(); };
      li.append(t, rv); list.append(li);
    }
  }
  btn.onclick = () => { panel.hidden = !panel.hidden; if (!panel.hidden) load(); };
  panel.querySelector('.sp-close').onclick = () => { panel.hidden = true; };
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') panel.hidden = true; });
  panel.querySelectorAll('input[name=tc-vis]').forEach(r => r.onchange = () => post('/api/sharing', { visibility: r.value }));
  panel.querySelectorAll('.sp-make').forEach(b => b.onclick = async () => {
    const r = await post('/api/share', { game_id: b.dataset.scope === 'game' ? game : null });
    made.hidden = false; input.value = full(r.url); input.select(); load(); });
  made.querySelector('.sp-copy').onclick = async () => {
    try { await navigator.clipboard.writeText(input.value); made.querySelector('.sp-copy').textContent = 'Copied'; }
    catch (e) { input.select(); } };
})();
"""
