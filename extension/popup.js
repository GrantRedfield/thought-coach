const DEFAULT_SERVER = 'https://thoughtcoach.org';
const LOCAL = /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/;
const $ = (id) => document.getElementById(id);
const LABEL = {
  uploading: 'sending\u2026',
  'waiting for server': 'waiting for the server',
  waiting: 'waiting to send',
  queued: 'queued for review',
  running: 'reviewing\u2026',
  done: 'reviewed',
  error: 'review failed',
  rejected: 'not accepted',
};

async function server() {
  const { server } = await chrome.storage.sync.get({ server: DEFAULT_SERVER });
  return server.replace(/\/+$/, '');
}

async function checkServer() {
  const url = await server();
  const { user, name, key } = await chrome.storage.sync.get({ user: null, name: null, key: '' });
  const local = LOCAL.test(url);
  $('server').value = url;
  let ok = false;
  try {
    const r = await fetch(`${url}/health`);
    ok = r.ok && (await r.json()).app === 'thoughtcoach';
    $('server-msg').textContent = ok ? `${url} is a Thought Coach server.` : 'That address is not a Thought Coach server.';
  } catch (err) {
    $('server-msg').innerHTML = local ? 'Not reachable. Start it with <code>python -m thoughtcoach serve</code>' : `Can't reach ${url}.`;
  }
  const connected = ok && (local || (user && key));
  $('conn').className = `dot ${connected ? 'ok' : 'bad'}`;
  $('account').textContent = local ? (ok ? 'Using the server on this PC.' : 'The server on this PC is not running.')
    : connected ? `Connected as ${name || user}.` : ok ? 'Not connected yet.' : `Can't reach ${new URL(url).host}.`;
  $('setup-row').hidden = local || connected;
  if (connected && (await dropStale())) showGames();
}

/** Remove games from the list, and any recording kept for them. */
async function forget(ids) {
  const { history = [] } = await chrome.storage.local.get('history');
  await chrome.storage.local.set({ history: history.filter((h) => !ids.includes(h.game_id)) });
  await chrome.storage.local.remove(ids.map((id) => `kept:${id}`));
}

/** Once connected, "not connected" refusals with no recording kept can't be sent again: drop them. */
async function dropStale() {
  const all = await chrome.storage.local.get(null);
  const stale = (all.history || []).filter((h) => h.state === 'rejected' && /^not connected/.test(h.error || '')
    && !all[`kept:${h.game_id}`]);
  if (stale.length) await forget(stale.map((h) => h.game_id));
  return stale.length;
}

async function showGames() {
  const { history = [] } = await chrome.storage.local.get('history');
  const list = $('games');
  if (history.length) list.textContent = '';
  for (const h of history.slice(0, 8)) {
    const li = document.createElement('li');
    const id = document.createElement('a');
    id.className = 'id';
    id.href = `https://lichess.org/${h.game_id}`;
    id.target = '_blank';
    id.textContent = h.game_id;
    const st = document.createElement('span');
    st.className = 'st';
    st.textContent = LABEL[h.state] || h.state;
    if (h.error && h.state !== 'done') st.title = h.error;
    li.append(id, st);
    if (['queued', 'running'].includes(h.state) && h.live) {
      const a = document.createElement('a');
      a.href = h.live;
      a.target = '_blank';
      a.textContent = 'watch live';
      li.append(a);
    }
    if (h.state === 'waiting' && h.error) {
      const why = document.createElement('div');
      why.className = 'why wait';
      why.textContent = `${h.error.replace(/\.$/, '')}. It will be sent again by itself.`;
      li.append(why);
    }
    if (h.state === 'rejected') {
      const kept = await chrome.storage.local.get(`kept:${h.game_id}`);
      if (kept[`kept:${h.game_id}`]) {
        const a = document.createElement('a');
        a.href = '#';
        a.textContent = 'send again';
        a.onclick = async (e) => {
          e.preventDefault();
          st.textContent = 'sending\u2026';
          await chrome.runtime.sendMessage({ type: 'resend', game_id: h.game_id });
          showGames();
        };
        li.append(a);
      }
      const x = document.createElement('a');
      x.href = '#';
      x.textContent = 'dismiss';
      x.title = 'Remove this game from the list';
      x.onclick = async (e) => {
        e.preventDefault();
        await forget([h.game_id]);
        showGames();
      };
      li.append(x);
      const why = document.createElement('div');
      why.className = 'why';
      why.textContent = h.error || '';
      li.append(why);
    }
    if (h.state === 'done') {
      const a = document.createElement('a');
      a.href = h.dashboard || `${await server()}/`;
      a.target = '_blank';
      a.textContent = 'review';
      li.append(a);
    }
    list.append(li);
  }
  const all = await chrome.storage.local.get(null);
  const pending = Object.keys(all).filter((k) => k.startsWith('pending:')).length;
  $('retry-row').hidden = !pending;
  $('pending').textContent = `${pending} game${pending === 1 ? '' : 's'} waiting to send`;
}

async function showTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !/^https:\/\/lichess\.org\//.test(tab.url || '')) return;
  try {
    const s = await chrome.tabs.sendMessage(tab.id, { type: 'status' });
    if (!s.game_id) {
      $('tab').textContent = 'Not a game page.';
    } else if (!s.has_recording) {
      $('tab').textContent = `Game ${s.game_id}: nothing recorded.`;
    } else {
      $('tab').textContent = s.ended ? `Game ${s.game_id}: recorded, game over.` : `Game ${s.game_id}: recording.`;
      $('tab-actions').hidden = !s.ended;
    }
    $('tab').title = s.text;
    $('send').onclick = async () => {
      const r = await chrome.tabs.sendMessage(tab.id, { type: 'send_again' });
      $('tab').textContent = r.ok ? 'Sent.' : r.error;
      showGames();
    };
    $('download').onclick = () => chrome.tabs.sendMessage(tab.id, { type: 'download' });
  } catch (err) {
    $('tab').textContent = 'Reload the lichess tab to start recording.';
  }
}

$('voice').onchange = (e) => chrome.storage.sync.set({ voice_enabled: e.target.checked });
$('save').onclick = async () => {
  const url = $('server').value.trim().replace(/\/+$/, '') || DEFAULT_SERVER;
  if (!LOCAL.test(url) && url !== DEFAULT_SERVER) {
    // The extension only talks to thoughtcoach.org or a server on this PC (its permissions say so).
    $('server-msg').textContent = 'Use https://thoughtcoach.org or a server on this PC (http://127.0.0.1:<port>).';
    return;
  }
  await chrome.storage.sync.set({ server: url });
  checkServer();
};
$('save-key').onclick = async () => {
  await chrome.storage.sync.set({ key: $('key').value.trim() });
  $('server-msg').textContent = 'Upload key saved.';
};
$('retry').onclick = async () => {
  await chrome.runtime.sendMessage({ type: 'retry' });
  showGames();
};
$('open').onclick = async (e) => {
  e.preventDefault();
  const { home } = await chrome.storage.sync.get({ home: null });
  chrome.tabs.create({ url: home || `${await server()}/` });
};
$('setup').onclick = async () => chrome.tabs.create({ url: `${await server()}/start` });

(async () => {
  $('voice').checked = (await chrome.storage.sync.get({ voice_enabled: true })).voice_enabled;
  $('key').value = (await chrome.storage.sync.get({ key: '' })).key;
  checkServer();
  showGames();
  showTab();
})();
