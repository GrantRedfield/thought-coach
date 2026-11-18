const DEFAULT_SERVER = 'https://thoughtcoach.org';
const LOCAL = /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/;
const $ = (id) => document.getElementById(id);
const LABEL = {
  uploading: 'sending…',
  'waiting for server': 'waiting for the server',
  waiting: 'waiting (game not over on lichess yet)',
  queued: 'queued for review',
  running: 'reviewing…',
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
    if (h.state === 'rejected') {
      const kept = await chrome.storage.local.get(`kept:${h.game_id}`);
      if (kept[`kept:${h.game_id}`]) {
        const a = document.createElement('a');
        a.href = '#';
        a.textContent = 'send again';
        a.onclick = async (e) => {
          e.preventDefault();
          st.textContent = 'sending�';
          await chrome.runtime.sendMessage({ type: 'resend', game_id: h.game_id });
          showGames();
        };
        li.append(a);
      }
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
