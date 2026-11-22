/*
 * Thought Coach - on thoughtcoach.org only: lets the setup page see that the extension is
 * installed, connects it to the signed-in player (saves their upload key and the server), and lets
 * the page switch voice recording on or off (the same setting as the popup's checkbox).
 * Nothing here runs on lichess.
 */
(() => {
  const version = chrome.runtime.getManifest().version;

  async function answer(type) {
    const { user = null, voice_enabled: voice } = await chrome.storage.sync.get({ user: null, voice_enabled: true });
    window.postMessage({ tc: 'ext', type, version, user, voice }, location.origin);
  }

  window.addEventListener('message', async (e) => {
    if (e.source !== window || e.origin !== location.origin || !e.data || e.data.tc !== 'site') return;
    const m = e.data;
    if (m.type === 'hello') return answer('hello');
    if (m.type === 'connect' && typeof m.key === 'string' && m.key.length >= 20 && /^[a-z0-9_-]{2,30}$/.test(m.user || '')) {
      await chrome.storage.sync.set({ server: location.origin, key: m.key, user: m.user, name: m.name || m.user,
        home: `${location.origin}${m.home || `/u/${m.user}/`}` });
      answer('connected');
    }
    if (m.type === 'voice' && typeof m.on === 'boolean') {
      await chrome.storage.sync.set({ voice_enabled: m.on });  // (a game in progress stops recording at once)
      answer('voice');
    }
  });
  answer('hello');
})();
