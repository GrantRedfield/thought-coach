/*
 * Thought Coach - background: uploads finished games to Thought Coach (thoughtcoach.org, or your own PC).
 *
 * The content script hands over a recording only after the game is over. It is kept in
 * extension storage ('pending:<id>') until the server accepts it, so nothing is lost if the
 * server is down: it is retried every minute. The server also refuses a game lichess still
 * shows as in progress (409); that one is retried too.
 *
 * Once accepted, the review's progress is checked every 30 s and a notification links to the
 * dashboard when it is ready.
 */

const DEFAULT_SERVER = 'https://thoughtcoach.org';
const HISTORY_MAX = 30;

async function serverUrl() {
  const { server } = await chrome.storage.sync.get({ server: DEFAULT_SERVER });
  return server.replace(/\/+$/, '');
}

async function history() {
  const { history = [] } = await chrome.storage.local.get('history');
  return history;
}

/** Record what happened to a game (newest first). */
async function note(gameId, fields) {
  const list = await history();
  const old = list.find((h) => h.game_id === gameId) || { game_id: gameId };
  const entry = { ...old, ...fields, at: Date.now() };
  const rest = list.filter((h) => h.game_id !== gameId);
  await chrome.storage.local.set({ history: [entry, ...rest].slice(0, HISTORY_MAX) });
  return entry;
}

async function pendingIds() {
  const all = await chrome.storage.local.get(null);
  return Object.keys(all).filter((k) => k.startsWith('pending:')).map((k) => k.slice(8));
}

const LOCAL = /^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/;

/**
 * Send a recording. Your own PC's server takes it in one request; the hosted service takes it
 * in three (recordings with voice are too big for one): ask for a one-time signed upload link,
 * put the file straight into storage, then say it's there. Returns the last response.
 */
async function send(server, recording) {
  const { key = '' } = await chrome.storage.sync.get({ key: '' });
  const headers = { 'Content-Type': 'application/json', 'X-Thought-Coach-Key': key };
  if (LOCAL.test(server)) {
    return fetch(`${server}/api/recordings`, { method: 'POST', headers, body: JSON.stringify(recording) });
  }
  const id = recording.game_id;
  const link = await fetch(`${server}/api/uploads`, { method: 'POST', headers, body: JSON.stringify({ game_id: id }) });
  if (!link.ok) return link;
  const { put_url: putUrl, upload_id: uploadId } = await link.json();
  const put = await fetch(putUrl, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(recording) });
  if (!put.ok) return new Response(JSON.stringify({ error: `storage upload failed (${put.status})` }), { status: 502 });
  return fetch(`${server}/api/recordings/${id}/uploaded`, { method: 'POST', headers, body: JSON.stringify({ upload_id: uploadId }) });
}

async function upload(gameId) {
  const key = `pending:${gameId}`;
  const { [key]: recording } = await chrome.storage.local.get(key);
  if (!recording) return;
  const server = await serverUrl();
  let response;
  try {
    response = await send(server, recording);
  } catch (err) {
    await note(gameId, { state: 'waiting for server', error: `can't reach ${server}` });
    return schedule();
  }
  const body = await response.json().catch(() => ({}));
  if (response.status === 202) {
    await chrome.storage.local.remove(key);
    const live = body.live_url ? server + body.live_url : null;
    await note(gameId, { state: body.state || 'queued', error: null, dashboard: null,
      status_url: body.status_url || null, story_url: body.story_url || null, live });
    if (live && !body.kept) {
      chrome.notifications.create(`live:${gameId}`, {
        type: 'basic',
        iconUrl: 'icon128.png',
        title: 'Thought Coach: reviewing your game',
        message: 'Your review has started. Click to watch it come together live.',
      });
    }
  } else if (response.status === 401) {
    await note(gameId, { state: 'waiting', error: 'not connected: open thoughtcoach.org/start and sign in' });
  } else if (response.status === 409 || response.status === 429 || response.status >= 500) {
    await note(gameId, { state: 'waiting', error: body.error || `server said ${response.status}` });
  } else {
    // Not accepted (e.g. an abandoned game). Kept for two weeks, not retried by itself: the popup
    // can send it again, so a refusal that was a mistake never loses the recording (or the voice).
    await chrome.storage.local.set({ [`kept:${gameId}`]: { recording, at: Date.now() } });
    await chrome.storage.local.remove(key);
    await note(gameId, { state: 'rejected', error: body.error || `server said ${response.status}` });
  }
  schedule();
}

/** Check reviews in progress; notify when one is done. */
async function poll() {
  const server = await serverUrl();
  for (const h of await history()) {
    if (!['queued', 'running'].includes(h.state)) continue;
    try {
      const s = await (await fetch(`${server}${h.status_url || `/api/games/${h.game_id}`}`)).json();
      if (s.state === h.state) continue;
      const entry = await note(h.game_id, { state: s.state, error: s.error || null,
        dashboard: s.story ? server + s.story : s.dashboard ? server + s.dashboard : null });
      if (entry.state === 'done') {
        chrome.notifications.create(`done:${h.game_id}`, {
          type: 'basic',
          iconUrl: 'icon128.png',
          title: 'Thought Coach: review ready',
          message: `Your game ${h.game_id} is reviewed. Click to watch your game story.`,
        });
      }
    } catch (err) {
      // server down: try again on the next alarm
    }
  }
}

/** Keep an alarm running while anything is waiting to upload or being reviewed. */
async function schedule() {
  const busy = (await pendingIds()).length || (await history()).some((h) => ['queued', 'running'].includes(h.state));
  if (busy) chrome.alarms.create('tick', { periodInMinutes: 0.5 });
  else chrome.alarms.clear('tick');
}

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== 'tick') return;
  for (const id of await pendingIds()) await upload(id);
  await poll();
  await schedule();
});

chrome.notifications.onClicked.addListener(async (id) => {
  if (id.startsWith('live:')) {
    const h = (await history()).find((x) => x.game_id === id.slice(5));
    if (h && h.live) chrome.tabs.create({ url: h.live });
    chrome.notifications.clear(id);
    return;
  }
  if (!id.startsWith('done:')) return;
  const gameId = id.slice(5);
  const h = (await history()).find((x) => x.game_id === gameId);
  chrome.tabs.create({ url: (h && h.dashboard) || `${await serverUrl()}/` });
  chrome.notifications.clear(id);
});

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type === 'upload' && msg.recording && msg.recording.game_id) {
    const id = msg.recording.game_id;
    chrome.storage.local
      .set({ [`pending:${id}`]: msg.recording })
      .then(() => note(id, { state: 'uploading', error: null }))
      .then(() => {
        reply({ saved: true });
        upload(id);
      });
    return true;
  }
  if (msg.type === 'resend' && msg.game_id) {
    const k = `kept:${msg.game_id}`;
    chrome.storage.local.get(k).then(async ({ [k]: kept }) => {
      if (!kept) return reply({ ok: false, error: 'no recording kept for this game' });
      await chrome.storage.local.set({ [`pending:${msg.game_id}`]: kept.recording });
      await chrome.storage.local.remove(k);
      await note(msg.game_id, { state: 'uploading', error: null });
      await upload(msg.game_id);
      reply({ ok: true });
    });
    return true;
  }
  if (msg.type === 'should_ask') {
    // Ask before reviewing, unless switched off in the popup or "don't ask again" this session.
    Promise.all([chrome.storage.sync.get({ ask_before_review: true }), chrome.storage.session.get({ quiet: false })])
      .then(([sync, session]) => reply({ ask: sync.ask_before_review && !session.quiet }));
    return true;
  }
  if (msg.type === 'quiet_session') {
    chrome.storage.session.set({ quiet: true }).then(() => reply({ ok: true }));
    return true;
  }
  if (msg.type === 'dismiss' && msg.game_id) {
    forget([msg.game_id]).then(() => reply({ ok: true }));
    return true;
  }
  if (msg.type === 'retry') {
    pendingIds()
      .then((ids) => Promise.all(ids.map(upload)))
      .then(poll)
      .then(() => reply({ ok: true }));
    return true;
  }
  return false;
});

/** Remove games from the list (and any recording kept for them). */
async function forget(ids) {
  const list = await history();
  await chrome.storage.local.set({ history: list.filter((h) => !ids.includes(h.game_id)) });
  await chrome.storage.local.remove(ids.map((id) => `kept:${id}`));
}

/**
 * Once connected: send what was waiting for it, and drop "not connected" refusals left by older
 * versions (they did not keep the recording, so there is nothing to send again).
 */
async function onConnected() {
  const all = await chrome.storage.local.get(null);
  const stale = (all.history || []).filter((h) => h.state === 'rejected' && /^not connected/.test(h.error || '')
    && !all[`kept:${h.game_id}`]);
  if (stale.length) await forget(stale.map((h) => h.game_id));
  for (const id of await pendingIds()) await upload(id);
  schedule();
}

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'sync' && changes.key && changes.key.newValue) onConnected();
});

/** Drop refused recordings after two weeks. */
async function pruneKept() {
  const all = await chrome.storage.local.get(null);
  const old = Object.keys(all).filter((k) => k.startsWith('kept:') && Date.now() - (all[k].at || 0) > 14 * 864e5);
  if (old.length) await chrome.storage.local.remove(old);
}

chrome.runtime.onStartup.addListener(pruneKept);
chrome.runtime.onStartup.addListener(async () => {
  if ((await chrome.storage.sync.get({ key: '' })).key) onConnected();
});
chrome.runtime.onStartup.addListener(schedule);
chrome.runtime.onInstalled.addListener(async ({ reason }) => {
  schedule();
  if (reason === 'update' && (await chrome.storage.sync.get({ key: '' })).key) onConnected();
  if (reason !== 'install') return;
  // Straight to setup: reload an open setup page (so it sees the extension) or open one.
  const tabs = await chrome.tabs.query({ url: 'https://thoughtcoach.org/start*' }).catch(() => []);
  if (tabs.length) {
    tabs.forEach((tab) => chrome.tabs.reload(tab.id));
    chrome.tabs.update(tabs[0].id, { active: true });
  } else {
    chrome.tabs.create({ url: 'https://thoughtcoach.org/start' });
  }
});
