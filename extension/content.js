/*
 * Thought Coach - recorder (browser extension content script).
 *
 * FAIR PLAY: this script only READS the board. It shows nothing, adds nothing to the page and never
 * touches the moves. When the game is over, the recording (arrows, piece pickups, voice) is handed
 * to the extension, which uploads it to your Thought Coach server. The server also checks with
 * lichess that the game is over before it analyses anything.
 *
 * Recorded events (same format as the userscript, 'thoughtcoach-arrows/2'):
 *   position, shape_add / shape_remove, pick_up / put_down, game_end
 * Every event has a wall-clock timestamp `t`, `positions_seen` and the current `placement`.
 */

(function () {
  'use strict';

  const VERSION = chrome.runtime.getManifest().version;
  const FORMAT = 'thoughtcoach-arrows/2';
  const STORE_PREFIX = 'game:';
  const BRUSHES = ['green', 'red', 'yellow', 'blue']; // the user's brushes (lichess's own shapes use others)
  const STROKE_TO_BRUSH = { '#15781b': 'green', '#882020': 'red', '#e68f00': 'yellow', '#003088': 'blue' };
  const PIECE_LETTER = { pawn: 'p', knight: 'n', bishop: 'b', rook: 'r', queen: 'q', king: 'k' };
  const TRANSLATE = /translate\(\s*(-?[\d.]+)px\s*,\s*(-?[\d.]+)px\s*\)/;
  const CLOCKS_STOPPED_MS = 5000; // timed games: clocks stopped this long => game over
  const HOVER_MS = 700; // holding a picked-up piece over a square this long counts as considering that move

  // ------------------------------------------------------------------ storage (extension)
  //
  // Recordings live in the extension's own storage (not the page's). Reads come from a copy loaded
  // at start-up; writes go straight through.

  const cache = new Map();
  const settings = { voice_enabled: true };
  const store = {
    get: (k) => (cache.has(k) ? cache.get(k) : null),
    set: (k, v) => {
      cache.set(k, v);
      chrome.storage.local.set({ [k]: v });
    },
    keys: () => [...cache.keys()],
    del: (k) => {
      cache.delete(k);
      chrome.storage.local.remove(k);
    },
  };

  async function loadStorage() {
    const all = await chrome.storage.local.get(null);
    for (const [k, v] of Object.entries(all)) if (k.startsWith(STORE_PREFIX)) cache.set(k, v);
    Object.assign(settings, await chrome.storage.sync.get({ voice_enabled: true }));
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === 'sync' && changes.voice_enabled) {
        settings.voice_enabled = changes.voice_enabled.newValue;
        if (!settings.voice_enabled) stopVoice();
      }
    });
  }

  // ------------------------------------------------------------------ reading the page

  /** 8-character lichess game id. The 12-character URL form contains your secret player token,
   *  so only the first 8 characters are ever stored. */
  function gameIdFromUrl() {
    // 1. The page's own public link to the game (no player token in it).
    for (const sel of ['meta[property="og:url"]', 'link[rel="canonical"]']) {
      const el = document.querySelector(sel);
      const url = el && (el.getAttribute('content') || el.getAttribute('href'));
      const m = url && /lichess\.org\/(?:game\/)?([a-zA-Z0-9]{8})(?:[a-zA-Z0-9]{4})?(?:[/?#]|$)/.exec(url);
      if (m) return m[1];
    }
    // 2. The URL path: lichess uses both /<id>[token] and /game/<id>.
    const m = /^\/(?:game\/)?([a-zA-Z0-9]{8})[a-zA-Z0-9]*(?:[/?#]|$)/.exec(location.pathname);
    return m ? m[1] : null;
  }

  /** Shape of the URL path with every letter/digit replaced by x, for diagnosis without the token. */
  function pathShape() {
    return location.pathname.replace(/[a-zA-Z0-9]/g, 'x');
  }

  function isGamePage() {
    return !!document.querySelector('main.round, .round__app, .rclock');
  }

  /** The main board: the largest chessground board on the page (ignores mini boards). */
  function findBoardWrap() {
    let best = null;
    for (const wrap of document.querySelectorAll('main .cg-wrap')) {
      if (!best || wrap.offsetWidth > best.offsetWidth) best = wrap;
    }
    return best && best.querySelector('cg-board') ? best : null;
  }

  function orientation(wrap) {
    return wrap.classList.contains('orientation-black') ? 'black' : 'white';
  }

  /** Your colour: when you are playing, your clock is the one at the bottom. */
  function myColor(wrap) {
    const bottom = document.querySelector('.rclock-bottom');
    if (bottom && bottom.classList.contains('rclock-white')) return 'white';
    if (bottom && bottom.classList.contains('rclock-black')) return 'black';
    return orientation(wrap);
  }

  /**
   * Piece placement as the board part of a FEN, read from where the pieces are drawn.
   * Returns null while the board is mid-change (animation, drag) or doesn't look like a real
   * position, so we never record a half-finished state; the next check picks up the final one.
   */
  function placement(wrap) {
    const board = wrap.querySelector('cg-board');
    // Measure columns and rows separately: on lichess's 3D board the cells are not square.
    const colSize = board.offsetWidth / 8;
    const rowSize = board.offsetHeight / 8;
    if (!colSize || !rowSize) return null;
    const white = orientation(wrap) === 'white';
    const grid = Array.from({ length: 8 }, () => Array(8).fill(null)); // grid[rank][file]
    let kings = 0;
    for (const piece of board.querySelectorAll('piece')) {
      const cls = piece.classList;
      if (cls.contains('ghost') || cls.contains('fading')) continue; // drag shadow / captured piece fading out
      if (cls.contains('anim') || cls.contains('dragging')) return null;
      const color = cls.contains('white') ? 'white' : cls.contains('black') ? 'black' : null;
      const type = Object.keys(PIECE_LETTER).find((t) => cls.contains(t));
      const m = TRANSLATE.exec(piece.style.transform);
      if (!color || !type || !m) continue;
      const col = Math.round(parseFloat(m[1]) / colSize);
      const row = Math.round(parseFloat(m[2]) / rowSize);
      const file = white ? col : 7 - col;
      const rank = white ? 7 - row : row;
      if (file < 0 || file > 7 || rank < 0 || rank > 7 || grid[rank][file]) return null;
      const letter = PIECE_LETTER[type];
      grid[rank][file] = color === 'white' ? letter.toUpperCase() : letter;
      if (type === 'king') kings += 1;
    }
    if (kings !== 2) return null;
    const rows = [];
    for (let rank = 7; rank >= 0; rank--) {
      let row = '';
      let empty = 0;
      for (let file = 0; file < 8; file++) {
        const p = grid[rank][file];
        if (p) {
          if (empty) row += empty;
          row += p;
          empty = 0;
        } else {
          empty += 1;
        }
      }
      rows.push(empty ? row + empty : row);
    }
    return rows.join('/');
  }

  /** The square of the piece you have picked up (lichess highlights it as square.selected), or null. */
  function selectedSquare(wrap) {
    const board = wrap.querySelector('cg-board');
    const el = board && board.querySelector('square.selected');
    const m = el && TRANSLATE.exec(el.style.transform);
    const colSize = board ? board.offsetWidth / 8 : 0;
    const rowSize = board ? board.offsetHeight / 8 : 0;
    if (!m || !colSize || !rowSize) return null;
    const col = Math.round(parseFloat(m[1]) / colSize);
    const row = Math.round(parseFloat(m[2]) / rowSize);
    const white = orientation(wrap) === 'white';
    const file = white ? col : 7 - col;
    const rank = white ? 7 - row : row;
    if (file < 0 || file > 7 || rank < 0 || rank > 7) return null;
    return 'abcdefgh'[file] + (rank + 1);
  }

  /** The square under the pointer, or null (same board geometry as selectedSquare). */
  function squareAt(wrap, x, y) {
    const board = wrap.querySelector('cg-board');
    if (!board) return null;
    const r = board.getBoundingClientRect();
    if (x < r.left || y < r.top || x >= r.right || y >= r.bottom) return null;
    const col = Math.floor((x - r.left) / (r.width / 8));
    const row = Math.floor((y - r.top) / (r.height / 8));
    const white = orientation(wrap) === 'white';
    const file = white ? col : 7 - col;
    const rank = white ? 7 - row : row;
    return 'abcdefgh'[file] + (rank + 1);
  }

  /** The pointer left a square while a piece was held: a long enough stay is a move considered. */
  function endHover(rec, now) {
    const h = rec.hover;
    rec.hover = null;
    if (!h || !rec.selected || h.square === rec.selected || h.from !== rec.selected) return;
    const ms = now - h.since;
    if (ms >= HOVER_MS) push(rec, { type: 'hover', from: h.from, to: h.square, ms: Math.round(ms) });
  }

  /** One user-drawn shape from a chessground <g cgHash="w,h,[true,][n,]orig[,dest],brush">. */
  function parseShape(g) {
    const hash = g.getAttribute('cgHash');
    if (!hash) return null;
    const tokens = hash.split(',');
    if (tokens.includes('true')) return null; // still being dragged
    const squares = tokens.filter((t) => /^[a-h][1-8]$/.test(t));
    let brush = tokens.find((t) => BRUSHES.includes(t));
    if (!brush) {
      const el = g.querySelector('[stroke]');
      brush = el && STROKE_TO_BRUSH[(el.getAttribute('stroke') || '').toLowerCase()];
    }
    if (!brush || squares.length === 0) return null; // lichess's own auto-shapes use other brushes
    return { from: squares[0], to: squares[1] || null, brush };
  }

  function currentShapes(wrap) {
    const shapes = new Map();
    for (const g of wrap.querySelectorAll('svg.cg-shapes g')) {
      const s = parseShape(g);
      if (s) shapes.set(`${s.from}-${s.to || ''}-${s.brush}`, s);
    }
    return shapes;
  }

  function clocks() {
    const text = (sel) => {
      const el = document.querySelector(sel);
      return el ? el.textContent.replace(/\s+/g, '') : null;
    };
    const running = document.querySelector('.rclock.running');
    return {
      clock_white: text('.rclock-white'),
      clock_black: text('.rclock-black'),
      running: running ? (running.classList.contains('rclock-white') ? 'white' : 'black') : null,
    };
  }

  function isGameOver(rec) {
    if (document.querySelector('.result-wrap, .follow-up, button.rematch')) return true;
    // Fallback for timed games only: clocks were running and have stopped. Unlimited games also
    // have clock elements, but they never run, so require having seen a running clock first.
    const running = !!document.querySelector('.rclock.running');
    if (running) rec.clockSeenRunning = true;
    if (rec.clockSeenRunning && !running) {
      if (!rec.clocksStoppedSince) rec.clocksStoppedSince = Date.now();
      return Date.now() - rec.clocksStoppedSince > CLOCKS_STOPPED_MS;
    }
    rec.clocksStoppedSince = null;
    return false;
  }

  // ------------------------------------------------------------------ voice (microphone)
  //
  // While you are PLAYING a game (never when watching), the microphone is recorded in 5-second
  // chunks into this browser's IndexedDB, so a page reload loses at most a few seconds. At the end
  // of the game the audio is added to the recording and uploaded with it to your Thought Coach
  // server; once the extension has the recording, the audio is deleted from this browser.

  const AUDIO_DB = 'thoughtcoach-audio';
  const voice = { recorder: null, stream: null, gameId: null, starting: false, error: null, pending: new Set() };

  function voiceEnabled() {
    return settings.voice_enabled !== false;
  }

  function openAudioDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(AUDIO_DB, 1);
      req.onupgradeneeded = () => req.result.createObjectStore('chunks', { autoIncrement: true }).createIndex('game', 'game');
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  async function saveChunk(chunk) {
    const db = await openAudioDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction('chunks', 'readwrite');
      tx.objectStore('chunks').add(chunk);
      tx.oncomplete = resolve;
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  }

  /** All audio for a game as segments (one per recording session, e.g. before/after a reload). */
  async function loadAudio(gameId) {
    const db = await openAudioDb();
    const chunks = await new Promise((resolve, reject) => {
      const req = db.transaction('chunks').objectStore('chunks').index('game').getAll(gameId);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    db.close();
    const segments = new Map();
    for (const c of chunks) {
      if (!segments.has(c.start_t)) segments.set(c.start_t, { start_t: c.start_t, mime: c.mime, parts: [] });
      segments.get(c.start_t).parts.push(c);
    }
    return [...segments.values()]
      .sort((a, b) => a.start_t - b.start_t)
      .map((s) => ({ start_t: s.start_t, mime: s.mime, blob: new Blob(s.parts.sort((a, b) => a.seq - b.seq).map((p) => p.blob), { type: s.mime }) }));
  }

  async function deleteAudio(gameId) {
    const db = await openAudioDb();
    await new Promise((resolve) => {
      const tx = db.transaction('chunks', 'readwrite');
      const req = tx.objectStore('chunks').index('game').openKeyCursor(IDBKeyRange.only(gameId));
      req.onsuccess = () => {
        const cursor = req.result;
        if (cursor) {
          tx.objectStore('chunks').delete(cursor.primaryKey);
          cursor.continue();
        }
      };
      tx.oncomplete = tx.onerror = resolve;
    });
    db.close();
  }

  async function deleteAllAudio() {
    await new Promise((resolve) => {
      const req = indexedDB.deleteDatabase(AUDIO_DB);
      req.onsuccess = req.onerror = req.onblocked = resolve;
    });
  }

  function blobToBase64(blob) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',')[1] || '');
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
  }

  /** You are a player in this game (not watching): the resign/abort buttons are only shown to players. */
  function isPlayer() {
    return !!document.querySelector('.rcontrols button.resign, .rcontrols button.abort');
  }

  async function startVoice(gameId) {
    if (!voiceEnabled() || voice.recorder || voice.starting) return;
    voice.starting = true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime, audioBitsPerSecond: 32000 } : undefined);
      const segment = { start_t: null, seq: 0 };
      recorder.onstart = () => {
        segment.start_t = Date.now();
      };
      recorder.ondataavailable = (e) => {
        if (!e.data || !e.data.size) return;
        const p = saveChunk({ game: gameId, start_t: segment.start_t, seq: segment.seq++, mime: recorder.mimeType, blob: e.data })
          .catch((err) => { voice.error = `saving audio: ${err}`; })
          .finally(() => voice.pending.delete(p));
        voice.pending.add(p);
      };
      recorder.start(5000);
      Object.assign(voice, { recorder, stream, gameId, error: null });
    } catch (err) {
      voice.error = String(err);
    } finally {
      voice.starting = false;
    }
  }

  /** Stop recording; resolves once every chunk is saved. Safe to call more than once. */
  function stopVoice() {
    const { recorder, stream } = voice;
    if (!recorder) return voice.stopping || Promise.resolve();
    voice.stopping = new Promise((resolve) => {
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        await Promise.all([...voice.pending]); // the last chunk is still being saved
        resolve();
      };
    });
    Object.assign(voice, { recorder: null, stream: null, gameId: null });
    recorder.stop();
    return voice.stopping;
  }

  window.addEventListener('pagehide', () => {
    if (voice.recorder) voice.recorder.requestData(); // keep the last few seconds on reload
    if (pendingSave) save(pendingSave.rec);  // and whatever was drawn since the last save
  });

  // ------------------------------------------------------------------ recording

  function newGameData(id, wrap) {
    return {
      format: FORMAT,
      script_version: VERSION,
      game_id: id,
      url: `https://lichess.org/${id}`,
      my_color: myColor(wrap),
      started_at: new Date().toISOString(),
      ended_at: null,
      exported: false,
      positions_seen: 0,
      placement: null,
      events: [],
    };
  }

  // Saving rewrites the whole recording, and Chrome's main process does the writing: batch it (at
  // most every SAVE_MS), and save at once when it matters (game over, leaving the page).
  const SAVE_MS = 2000;
  let pendingSave = null;

  function save(rec) {
    clearTimeout(pendingSave && pendingSave.timer);
    pendingSave = null;
    store.set(STORE_PREFIX + rec.data.game_id, JSON.stringify(rec.data));
  }

  function saveSoon(rec) {
    if (pendingSave) return;
    pendingSave = { rec, timer: setTimeout(() => save(rec), SAVE_MS) };
  }

  function push(rec, event) {
    const d = rec.data;
    d.events.push({ t: Date.now(), ...event, positions_seen: d.positions_seen, placement: d.placement });
    saveSoon(rec);
  }

  /** Compare the board with what we saw last time and log any differences. */
  function check(rec) {
    const wrap = rec.wrap;
    if (!wrap || !wrap.isConnected || rec.viewOnly || !rec.data) return;
    const d = rec.data;

    const color = myColor(wrap);
    if (color !== d.my_color && document.querySelector('.rclock-bottom')) d.my_color = color;

    const fen = placement(wrap);
    if (fen && fen !== d.placement) {
      if (d.ended_at) {
        // Board changed after a "game over" (e.g. a false alarm): keep going, export again at the real end.
        d.ended_at = null;
        d.exported = false;
        push(rec, { type: 'resume' });
      }
      d.positions_seen += 1;
      d.placement = fen;
      push(rec, { type: 'position', ...clocks() });
    }

    const shapes = currentShapes(wrap);
    for (const [key, s] of shapes) {
      if (!rec.shapes.has(key)) push(rec, { type: 'shape_add', ...s });
    }
    for (const [key, s] of rec.shapes) {
      if (!shapes.has(key)) push(rec, { type: 'shape_remove', ...s });
    }
    rec.shapes = shapes;

    // Picking up a piece (click or drag start) and letting go of it again.
    const selected = selectedSquare(wrap);
    if (selected !== rec.selected) {
      endHover(rec, Date.now());  // letting go (or moving) ends a hover too
      if (rec.selected) push(rec, { type: 'put_down', square: rec.selected });
      if (selected) push(rec, { type: 'pick_up', square: selected });
      rec.selected = selected;
    }

    if (!d.ended_at && isGameOver(rec)) {
      d.ended_at = new Date().toISOString();
      push(rec, { type: 'game_end' });
      if (!d.exported) {
        d.exported = true;
        save(rec);
        stopVoice().then(() => askThenUpload(d));
      }
    }
  }

  let tracking = false, trackRec = null;

  function attach(rec) {
    const wrap = findBoardWrap();
    if (!wrap || wrap === rec.wrap) return;
    if (rec.observer) rec.observer.disconnect();
    rec.wrap = wrap;
    if (!rec.data) {
      const saved = store.get(STORE_PREFIX + rec.id);
      const parsed = saved ? JSON.parse(saved) : null;
      if (!parsed && isGameOver(rec)) {
        // A finished game opened afterwards (its recording was already sent): stepping through
        // its moves is not playing it, so nothing is recorded and nothing is uploaded.
        rec.viewOnly = true;
        return;
      }
      // Recordings from older script versions use a different format: start a fresh one.
      rec.data = parsed && parsed.format === FORMAT ? parsed : newGameData(rec.id, wrap);
      rec.shapes = currentShapes(wrap);
    }
    // Piece animations cause many mutations per frame: check at most once per frame.
    rec.observer = new MutationObserver(() => {
      if (rec.scheduled) return;
      rec.scheduled = true;
      requestAnimationFrame(() => {
        rec.scheduled = false;
        check(rec);
      });
    });
    rec.observer.observe(wrap, { subtree: true, childList: true, attributes: true, attributeFilter: ['class', 'style', 'cgHash'] });
    // While a piece is held (dragged, or clicked and pointed with), follow the square under the pointer.
    trackRec = rec;
    if (tracking) return;  // the listeners stay; they follow whichever game this page shows
    tracking = true;
    const track = (e) => {
      const rec = trackRec;
      if (!rec) return;
      const wrap = rec.wrap;
      if (!rec.selected) { rec.hover = null; return; }
      const square = squareAt(wrap, e.clientX, e.clientY);
      if (rec.hover && rec.hover.square === square) return;
      endHover(rec, Date.now());
      if (square) rec.hover = { square, from: rec.selected, since: Date.now() };
    };
    document.addEventListener('pointermove', track, { passive: true });
    document.addEventListener('mousemove', track, { passive: true });  // some drags only send mouse events
  }

  // ------------------------------------------------------------------ export

  function download(obj, filename) {
    const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }

  /** The game's recording plus its audio, as one object. */
  async function withAudio(data) {
    let audio = [];
    try {
      for (const s of await loadAudio(data.game_id)) {
        audio.push({ start_t: s.start_t, mime: s.mime, data_base64: await blobToBase64(s.blob) });
      }
    } catch (err) {
      audio = [];
      voice.error = `reading audio: ${err}`;
    }
    return { ...data, audio };
  }

  async function exportGame(data) {
    download(await withAudio(data), `thoughtcoach-arrows-${data.game_id}.json`);
  }

  /** Hand the finished game to the extension (which uploads it), then free this browser's copy. */
  async function uploadGame(data) {
    const recording = await withAudio(data);
    try {
      const reply = await chrome.runtime.sendMessage({ type: 'upload', recording });
      if (reply && reply.saved) {
        store.del(STORE_PREFIX + data.game_id);
        await deleteAudio(data.game_id);
      }
    } catch (err) {
      voice.error = `handing over the recording: ${err}`; // kept here; the popup can send it again
    }
  }

  // ------------------------------------------------------------------ ask before reviewing
  //
  // Once the game is over (never during it), a small panel asks whether to review it. No answer in
  // ASK_SECONDS means yes, so a review isn't lost because nobody looked. "Don't ask again" holds
  // until Chrome is closed (the extension's session storage). A skipped game stays here: the
  // popup's "Send again" can still send it.

  const ASK_SECONDS = 20;

  async function askThenUpload(data) {
    let ask = true;
    try {
      ask = (await chrome.runtime.sendMessage({ type: 'should_ask' })).ask;
    } catch (err) {
      ask = false; // the extension was reloaded: just send it
    }
    if (!ask) return uploadGame(data);
    const answer = await askPanel();
    if (answer.quiet) chrome.runtime.sendMessage({ type: 'quiet_session' }).catch(() => {});
    if (answer.review) return uploadGame(data);
    data.skipped = true;
    store.set(STORE_PREFIX + data.game_id, JSON.stringify(data));
  }

  function askPanel() {
    return new Promise((resolve) => {
      const host = document.createElement('div');
      host.id = 'thoughtcoach-ask';
      const root = host.attachShadow({ mode: 'closed' });  // lichess's styles stay out
      root.innerHTML = `<style>
        .card { position:fixed; right:20px; bottom:20px; z-index:2147483647; width:320px; box-sizing:border-box;
          font:14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color:#f0efec; background:#1f1f1d;
          border:1px solid #3a3a37; border-radius:14px; padding:16px 16px 14px; box-shadow:0 12px 40px #0009;
          animation:in .25s ease both; }
        @keyframes in { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }
        @media (prefers-reduced-motion: reduce) { .card { animation:none; } }
        h2 { margin:0 0 4px; font-size:15px; display:flex; align-items:center; gap:8px; }
        h2 i { width:18px; height:18px; border-radius:4px; background:#2a78d6; display:inline-block; transform:rotate(45deg) scale(.7); }
        p { margin:0 0 12px; color:#c3c2b7; font-size:13px; }
        .row { display:flex; gap:8px; }
        button { font:inherit; font-size:13.5px; font-weight:600; border-radius:10px; padding:8px 12px; cursor:pointer; border:1px solid #3a3a37; }
        .yes { flex:1; background:#2a78d6; border-color:#2a78d6; color:#fff; }
        .no { background:transparent; color:#f0efec; }
        button:focus-visible, input:focus-visible { outline:2px solid #86b6ef; outline-offset:2px; }
        label { display:flex; gap:8px; align-items:center; margin-top:10px; font-size:12.5px; color:#c3c2b7; cursor:pointer; }
        .bar { height:3px; background:#3a3a37; border-radius:2px; margin-top:12px; overflow:hidden; }
        .bar span { display:block; height:100%; width:100%; background:#2a78d6; transform-origin:left; }
      </style>
      <div class="card" role="dialog" aria-labelledby="t" aria-describedby="d">
        <h2 id="t"><i></i>Review this game with Thought Coach?</h2>
        <p id="d">Your arrows${voiceEnabled() ? ', voice' : ''} and moves go to thoughtcoach.org. Your story is ready a few minutes later.</p>
        <div class="row"><button class="yes" type="button">Review it <span class="n"></span></button>
          <button class="no" type="button">Skip this game</button></div>
        <label><input type="checkbox" class="quiet"> Don't ask again until I close Chrome</label>
        <div class="bar"><span></span></div>
      </div>`;
      document.body.append(host);
      const yes = root.querySelector('.yes'), no = root.querySelector('.no'), quiet = root.querySelector('.quiet');
      const n = root.querySelector('.n'), bar = root.querySelector('.bar span');
      let left = ASK_SECONDS;
      const finish = (review) => {
        clearInterval(timer);
        host.remove();
        resolve({ review, quiet: quiet.checked });
      };
      const tick = () => {
        n.textContent = `(${left}s)`;
        bar.style.transform = `scaleX(${left / ASK_SECONDS})`;
        if (left-- <= 0) finish(true);
      };
      const timer = setInterval(tick, 1000);
      tick();
      quiet.addEventListener('change', () => { if (quiet.checked) { clearInterval(timer); n.textContent = ''; bar.parentElement.hidden = true; } });
      yes.addEventListener('click', () => finish(true));
      no.addEventListener('click', () => finish(false));
      yes.focus({ preventScroll: true });
    });
  }

  function storedGames() {
    return store.keys()
      .filter((k) => k.startsWith(STORE_PREFIX))
      .map((k) => JSON.parse(store.get(k)));
  }

  function storedGame(id) {
    const saved = id && store.get(STORE_PREFIX + id);
    return saved ? JSON.parse(saved) : null;
  }

  /** Why the script is or isn't recording. Never shows the full URL (it can hold your player token). */
  function statusText() {
    const id = gameIdFromUrl();
    const main = document.querySelector('main');
    const wrap = findBoardWrap();
    const stored = store.keys().filter((k) => k.startsWith(STORE_PREFIX)).length;
    return [
      `Thought Coach arrow recorder ${VERSION}`,
      `game id: ${id || 'none'}   (path shape ${pathShape()})`,
      `<main> class: "${main ? main.className : 'no <main>'}"`,
      `looks like a game page: ${isGamePage()}`,
      `board found: ${!!wrap}${wrap ? `, reads as a position: ${!!placement(wrap)}` : ''}`,
      `clocks found: ${document.querySelectorAll('.rclock').length}`,
      `you play: ${wrap ? myColor(wrap) : '?'}`,
      `recording now: ${current ? `${current.id}, ${current.data ? current.data.events.length : 0} events` : 'no'}`,
      `you are a player here: ${isPlayer()}`,
      `voice: ${!voiceEnabled() ? 'turned off' : voice.recorder ? `recording (${voice.gameId})` : 'not recording'}` +
        (voice.error ? `  [last error: ${voice.error}]` : ''),
      `games in storage: ${stored}`,
    ].join('\n');
  }

  /** Requests from the extension popup. */
  function listen() {
    chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
      const data = (current && current.data) || storedGame(gameIdFromUrl());
      if (msg.type === 'status') {
        reply({ text: statusText(), game_id: gameIdFromUrl(), has_recording: !!data, ended: !!(data && data.ended_at) });
      } else if (msg.type === 'send_again') {
        if (data && data.ended_at) uploadGame(data).then(() => reply({ ok: true }));
        else reply({ ok: false, error: data ? 'the game is not over yet' : 'no recording on this page' });
        return true;
      } else if (msg.type === 'download') {
        if (data) exportGame(data).then(() => reply({ ok: true }));
        else reply({ ok: false, error: 'no recording on this page' });
        return true;
      }
      return false;
    });
  }

  // ------------------------------------------------------------------ start

  let current = null; // the recording for the game on screen, if any

  /** Start (or switch) recording whenever a game page is on screen. Checked continuously, because
   *  the board can appear after the script starts and lichess can change pages without a reload. */
  function tick() {
    const id = gameIdFromUrl();
    if (id && isGamePage() && findBoardWrap()) {
      if (!current || current.id !== id) {
        if (current && current.observer) current.observer.disconnect();
        current = { id, data: null, wrap: null, observer: null, shapes: new Map(), clocksStoppedSince: null };
      }
      attach(current); // re-attaches if lichess re-rendered the board
      check(current); // also catches the end of the game
      // Voice: only while you are playing this game.
      const playing = isPlayer() && current.data && !current.data.ended_at;
      if (playing && !voice.recorder) startVoice(id);
      if (voice.recorder && (voice.gameId !== id || !playing) && !current.data.exported) stopVoice();
    } else {
      if (voice.recorder) stopVoice();
      if (current && current.id !== id) {
        if (current.observer) current.observer.disconnect();
        current = null;
      }
    }
  }

  async function main() {
    await loadStorage();
    listen();
    tick();
    setInterval(tick, 500);
  }

  // Exposed only for the test harness; lichess pages never set this.
  if (window.__thoughtcoachTest) {
    window.__thoughtcoachTest.api = { parseShape, placement, myColor, currentShapes, findBoardWrap, selectedSquare };
  } else {
    main();
  }
})();
