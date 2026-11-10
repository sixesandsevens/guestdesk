/* Run with a modern JS engine: eval this file, then await runYouTubeHarness(source). */
async function runYouTubeHarness(source) {
  function check(value, message) { if (!value) throw new Error(message); }
  const timers = new Map();
  let nextTimer = 1;
  globalThis.setTimeout = (callback, ms) => {
    const id = nextTimer++; timers.set(id, {callback, ms}); return id;
  };
  globalThis.clearTimeout = id => timers.delete(id);
  globalThis.document = {createElement() { return {setAttribute() {}}; }};
  const instances = [];
  const states = {PLAYING: 1, PAUSED: 2, ENDED: 0, BUFFERING: 3, CUED: 5};
  class MockPlayer {
    constructor(_mount, options) {
      this.options = options; this.calls = []; this.muted = false;
      this.destroyed = false; instances.push(this);
    }
    getIframe() { return {setAttribute() {}}; }
    setVolume(value) { this.calls.push(['volume', value]); }
    mute() { this.calls.push(['mute']); this.muted = true; }
    isMuted() { return this.muted; }
    loadVideoById(value) { this.calls.push(['load', value]); }
    playVideo() { this.calls.push(['play']); }
    stopVideo() { this.calls.push(['stop']); }
    destroy() { this.destroyed = true; this.calls.push(['destroy']); }
    ready() { this.options.events.onReady({target: this}); }
    state(state) { this.options.events.onStateChange({target: this, data: state}); }
    error(code) { this.options.events.onError({target: this, data: code}); }
    blocked() { this.options.events.onAutoplayBlocked(); }
  }
  globalThis.window = {YT: {Player: MockPlayer, PlayerState: states},
    location: {origin: 'https://guestdesk.info'}};
  eval(source);
  const host = {children: [], appendChild(child) { this.children.push(child); },
    replaceChildren() { this.children = []; }};
  const statuses = [], failures = [], endings = [];
  const player = new window.GuestDeskYouTubePlayer(host, {
    onStatus(value) { statuses.push(value); },
    onFailure(value) { failures.push(value); },
    onEnded() { endings.push(true); }
  });
  await player.start('M7lc1UVf-VE');
  check(instances.length === 1, 'player should be constructed');
  const first = instances[0];
  check(first.options.playerVars.cc_load_policy === 1 &&
    first.options.playerVars.cc_lang_pref === 'en' &&
    first.options.playerVars.controls === 0 &&
    first.options.playerVars.origin === 'https://guestdesk.info',
    'scheduled player must request captions, hide controls, and set origin');
  first.ready();
  check(first.calls[0][0] === 'volume' && first.calls[1][0] === 'mute' &&
    first.calls[2][0] === 'load' && first.calls[2][1].startSeconds === 0,
    'volume zero and mute must precede loading from the beginning');
  first.state(states.PLAYING);
  check(statuses.at(-1).muted === 'yes', 'playing must report mute');
  await player.start('dQw4w9WgXcQ');
  check(first.destroyed && instances.length === 2, 'loading another video must destroy old player');
  instances[1].ready();
  check(instances[1].calls[1][0] === 'mute', 'each video must be muted again');
  instances[1].error(150);
  check(failures.length === 1 && failures[0].includes('does not allow embedding') &&
    instances[1].destroyed && host.children.length === 0,
    'player error must fall back and clean up');

  await player.start('M7lc1UVf-VE');
  instances[2].ready();
  instances[2].blocked();
  check(failures.at(-1).includes('Autoplay blocked'), 'blocked autoplay must fail');
  await player.start('M7lc1UVf-VE');
  const timeout = [...timers.values()].find(timer => timer.ms === 15000);
  check(!!timeout, 'readiness timeout must be finite');
  timeout.callback();
  check(failures.at(-1).includes('readiness timed out'), 'readiness timeout must recover');
  await player.start('M7lc1UVf-VE');
  const last = instances.at(-1);
  last.ready(); last.state(states.PLAYING); last.state(states.BUFFERING);
  const bufferTimeout = [...timers.values()].find(timer => timer.ms === 20000);
  check(!!bufferTimeout, 'buffering timeout must be finite');
  bufferTimeout.callback();
  check(failures.at(-1).includes('stalled'), 'buffering stall must recover');
  await player.start('M7lc1UVf-VE');
  instances.at(-1).ready(); instances.at(-1).state(states.ENDED);
  check(endings.length === 1 && timers.size === 0, 'completion must clean timers and report end');

  globalThis.document = {
    createElement() { return {setAttribute() {}}; },
    head: {appendChild(script) { Promise.resolve().then(() => script.onerror()); }}
  };
  globalThis.window = {location: {origin: 'https://guestdesk.info'}};
  eval(source);
  const apiFailures = [];
  const broken = new window.GuestDeskYouTubePlayer(host, {
    onFailure(reason) { apiFailures.push(reason); }
  });
  await broken.start('M7lc1UVf-VE');
  check(apiFailures.length === 1 && apiFailures[0].includes('API could not load') &&
    timers.size === 0, 'API loading failure must return to signage and clean timers');
  return 'YouTube component harness passed';
}
runYouTubeHarness;
