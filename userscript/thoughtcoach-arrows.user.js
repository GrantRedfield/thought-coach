// ==UserScript==
// @name         Thought Coach - arrow recorder (read-only)
// @namespace    https://github.com/thoughtcoach
// @version      0.5.0
// @description  Records the arrows and circles you draw, and your voice, during your own lichess games and exports them as one JSON file after the game. It never shows analysis, never changes the page, and never uploads anything.
// @match        https://lichess.org/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_listValues
// @grant        GM_deleteValue
// @grant        GM_registerMenuCommand
// @run-at       document-idle
// @license      AGPL-3.0-or-later
// ==/UserScript==

/*
 * FAIR PLAY: this script only READS the board. It does not compute, fetch or display anything,
 * makes no network requests, adds nothing to the page, and never touches the moves. All analysis
 * happens later, offline, after the game is over.
 *
 * What it records (one JSON file per game):
 *   position                 : the piece placement (FEN board part) every time it changes, with
 *                              both clocks as displayed. Positions are matched to the PGN later, so
 *                              a position skipped by a premove does not shift anything.
 *   shape_add / shape_remove : arrows (from -> to) and circles (to = null) you draw
 *   pick_up / put_down       : a piece you pick up (click or start dragging) and let go of again,
 *                              by its square; read from lichess's own 'selected' square highlight
 *   game_end                 : when the game is over (the file is downloaded at that point)
 * Every event has a wall-clock timestamp `t` (ms since 1970), `positions_seen` and the current
 * `placement`.
 *
 * Draw normally: right-drag = arrow, right-click a square = circle. Colour is recorded but not
 * needed: what an arrow means is worked out later from whose piece it starts on.
 */

(function () {
  'use strict';

  const VERSION = '0.5.0';
  const FORMAT = 'thoughtcoach-arrows/2';
  const STORE_PREFIX = 'game:';
  const BRUSHES = ['green', 'red', 'yellow', 'blue']; // the user's brushes (lichess's own shapes use others)
  const STROKE_TO_BRUSH = { '#15781b': 'green', '#882020': 'red', '#e68f00': 'yellow', '#003088': 'blue' };
  const PIECE_LETTER = { pawn: 'p', knight: 'n', bishop: 'b', rook: 'r', queen: 'q', king: 'k' };
  const TRANSLATE = /translate\(\s*(-?[\d.]+)px\s*,\s*(-?[\d.]+)px\s*\)/;
  const CLOCKS_STOPPED_MS = 5000; // timed games: clocks stopped this long => game over

  // ------------------------------------------------------------------ storage (Tampermonkey)

  const store = {
    get: (k) => GM_getValue(k, null),
    set: (k, v) => GM_setValue(k, v),
    keys: () => GM_listValues(),
    del: (k) => GM_deleteValue(k),
  };

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
  // of the game the audio is put into the same JSON file as the arrows. Audio never leaves your
  // computer: there is no upload of any kind.

  const AUDIO_DB = 'thoughtcoach-audio';
  const voice = { recorder: null, stream: null, gameId: null, starting: false, error: null, pending: new Set() };

  function voiceEnabled() {
    return GM_getValue('voice_enabled', true);
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

  function save(rec) {
    store.set(STORE_PREFIX + rec.data.game_id, JSON.stringify(rec.data));
  }

  function push(rec, event) {
    const d = rec.data;
    d.events.push({ t: Date.now(), ...event, positions_seen: d.positions_seen, placement: d.placement });
    save(rec);
  }

  /** Compare the board with what we saw last time and log any differences. */
  function check(rec) {
    const wrap = rec.wrap;
    if (!wrap || !wrap.isConnected) return;
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
        stopVoice().then(() => exportGame(d));
      }
    }
  }

  function attach(rec) {
    const wrap = findBoardWrap();
    if (!wrap || wrap === rec.wrap) return;
    if (rec.observer) rec.observer.disconnect();
    rec.wrap = wrap;
    if (!rec.data) {
      const saved = store.get(STORE_PREFIX + rec.id);
      const parsed = saved ? JSON.parse(saved) : null;
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

  function registerMenu() {
    GM_registerMenuCommand('Export arrows + voice for this game', () => {
      const data = (current && current.data) || storedGame(gameIdFromUrl());
      if (data) exportGame(data);
      else alert('Thought Coach: no recording for this page.\n\n' + statusText());
    });
    GM_registerMenuCommand('Status', () => alert(statusText()));
    GM_registerMenuCommand('Turn voice recording on/off', () => {
      const on = !voiceEnabled();
      GM_setValue('voice_enabled', on);
      if (!on) stopVoice();
      alert(`Thought Coach: voice recording is now ${on ? 'ON (starts with your next game)' : 'OFF'}.`);
    });
    GM_registerMenuCommand('Export all recorded games', async () => {
      const games = [];
      for (const g of storedGames()) games.push(await withAudio(g));
      download({ format: 'thoughtcoach-arrows-bundle/1', games }, `thoughtcoach-arrows-all-${Date.now()}.json`);
    });
    GM_registerMenuCommand('Delete all recorded games (and voice)', async () => {
      const keys = store.keys().filter((k) => k.startsWith(STORE_PREFIX));
      if (confirm(`Thought Coach: permanently delete ${keys.length} recorded game(s) and all recorded voice?`)) {
        await stopVoice();
        keys.forEach(store.del);
        await deleteAllAudio();
      }
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

  function main() {
    registerMenu();
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
