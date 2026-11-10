/* Run with a modern JS engine: eval this file, then await runDisplayPlayerHarness(source). */
async function runDisplayPlayerHarness(source) {
  function check(value, message) { if (!value) throw new Error(message); }
  const video = (id, start, end, videoIds) => ({id, name: id, days: [0], start, end,
    videos: videoIds.map(video_id => ({title: video_id, video_id}))});

  async function scenario(windows, initialTime = '2026-06-01T09:15:00-04:00', preview = false) {
    let elapsed = 0;
    const intervals = new Map(), timeouts = new Map();
    let nextTimer = 1;
    function element(tag) {
      return {tagName: tag.toUpperCase(), children: [], style: {}, textContent: '',
        classList: {add() {}, remove() {}, toggle() {}},
        appendChild(child) { this.children.push(child); },
        append(...children) { this.children.push(...children); },
        replaceChildren(...children) { this.children = children; },
        querySelectorAll() { return []; }, querySelector() { return null; },
        addEventListener() {}, remove() {}};
    }
    const root = element('div'), host = element('div'), notice = element('div');
    globalThis.document = {
      getElementById(id) { return id === 'slide-container' ? root :
        id === 'youtube-container' ? host : notice; }, createElement: element
    };
    globalThis.performance = {now: () => elapsed};
    globalThis.setTimeout = (callback, ms) => {
      const id = nextTimer++; timeouts.set(id, {callback, ms}); return id;
    };
    globalThis.clearTimeout = id => timeouts.delete(id);
    globalThis.setInterval = (callback, ms) => {
      const id = nextTimer++; intervals.set(id, {callback, ms}); return id;
    };
    globalThis.clearInterval = id => intervals.delete(id);
    globalThis.console = {warn() {}};
    let payload = {server_time: initialTime, timezone: 'America/New_York',
      local_date: '2026-06-01', schedule_state: 'disabled', display: {name: 'Lobby'},
      slideshow: null, slides: [], video_windows: windows};
    globalThis.fetch = async () => ({ok: true, status: 200, json: async () => payload});
    const players = [];
    let pagehide = null;
    globalThis.window = {
      addEventListener(name, callback) { if (name === 'pagehide') pagehide = callback; },
      GUESTDESK_PLAYER: {url: '/api/display-slides/lobby', previewDuration: preview ? 2.5 : null,
        videoEnabled: !preview},
      GuestDeskYouTubePlayer: class {
        constructor(_host, callbacks, options) {
          this.callbacks = callbacks; this.options = options;
          this.starts = []; this.stops = 0; players.push(this);
        }
        start(id) { this.starts.push(id); }
        stop() { this.stops++; }
      }
    };
    eval(source);
    const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
    await settle();
    return {
      players, host, notice, root, intervals, timeouts, settle,
      pagehide() { pagehide(); },
      setPayload(value) { payload = value; },
      setAudio(enabled, volume) {
        payload = {...payload, display: {...payload.display,
          video_audio_enabled: enabled, video_volume: volume}};
      },
      async poll() {
        [...intervals.values()].find(timer => timer.ms === 60000).callback();
        await settle();
      },
      tick(ms) {
        elapsed = ms;
        const timer = [...intervals.values()].find(item => item.ms === 1000);
        if (timer) timer.callback();
      }
    };
  }

  const first = video('morning', '09:00', '09:30', ['M7lc1UVf-VE', 'dQw4w9WgXcQ']);
  const second = video('next', '09:30', '10:00', ['M7lc1UVf-VE']);
  const a = await scenario([first, second]);
  check(a.players.length === 1 && a.players[0].starts[0] === 'M7lc1UVf-VE',
    'starting inside a window must start its first video');
  await a.poll();
  check(a.players[0].starts.length === 1, 'unchanged polling must not restart video');
  check(a.players[0].options.audioEnabled === false && a.players[0].options.volume === 50,
    'old display configuration must default to muted playback');
  a.players[0].callbacks.onEnded();
  check(a.players[0].starts[1] === 'dQw4w9WgXcQ', 'completion must advance');
  a.tick(15 * 60 * 1000);
  check(a.players.length === 2 && a.players[1].starts[0] === 'M7lc1UVf-VE',
    'adjacent window must switch directly');
  a.tick(45 * 60 * 1000);
  check(a.host.style.display === 'none' && a.players[1].stops === 1,
    'window end must stop YouTube and return to slides');

  const b = await scenario([video('long', '09:00', '10:00',
    ['M7lc1UVf-VE', 'dQw4w9WgXcQ'])]);
  b.players[0].callbacks.onFailure('YouTube player error 150');
  check(b.players[0].starts[1] === 'dQw4w9WgXcQ', 'first failure must skip');
  b.players[0].callbacks.onFailure('Playback stalled');
  check(b.host.style.display === 'none' && b.notice.style.display === 'block',
    'all failures must show slideshow and a diagnostic');
  b.tick(60 * 1000);
  check(b.players.length === 1, 'cooldown must prevent rapid retries');
  b.tick(6 * 60 * 1000);
  check(b.players.length === 2, 'cooldown must retry automatically');

  const sound = await scenario([first]);
  sound.setAudio(true, 35);
  await sound.poll();
  check(sound.players.length === 2 && sound.players[0].stops === 1 &&
    sound.players[1].options.audioEnabled === true && sound.players[1].options.volume === 35,
    'changing sound settings must replace the active player');
  await sound.poll();
  check(sound.players.length === 2, 'unchanged sound settings must not restart playback');
  sound.players[1].callbacks.onFailure('Autoplay blocked by browser with sound enabled');
  check(sound.host.style.display === 'none' && sound.notice.style.display === 'block',
    'blocked sound autoplay must return to signage with a diagnostic');

  const c = await scenario([first]);
  c.players[0].callbacks.onFailure('Autoplay blocked by browser');
  check(c.host.style.display === 'none' && c.players[0].starts.length === 1,
    'blocked autoplay must return to signage without trying another video');

  const d = await scenario([first], '2026-06-01T09:15:00-04:00', true);
  check(d.players.length === 0, 'slideshow preview must not enter YouTube mode');

  const e = await scenario([first]);
  e.setPayload({server_time: '2026-06-01T09:15:00-04:00',
    timezone: 'America/New_York', local_date: '2026-06-01',
    schedule_state: 'disabled', display: {name: 'Lobby'}, slideshow: null,
    slides: [], video_windows: []});
  await e.poll();
  check(e.host.style.display === 'none' && e.players[0].stops === 1,
    'disabling a window must stop playback');

  const f = await scenario([first]);
  globalThis.fetch = async () => { throw new Error('network offline'); };
  await f.poll();
  f.tick(15 * 60 * 1000);
  check(f.host.style.display === 'none' && f.players[0].stops === 1,
    'known end must stop playback even when refresh fails');

  const g = await scenario([first]);
  g.setPayload({server_time: '2026-06-01T09:15:00-04:00',
    timezone: 'America/New_York', local_date: '2026-06-01',
    schedule_state: 'disabled', display: {name: 'Lobby'}, slideshow: null,
    slides: [], video_windows: [video('morning', '09:00', '09:30', ['dQw4w9WgXcQ'])]});
  await g.poll();
  check(g.players.length === 2 && g.players[0].stops === 1 &&
    g.players[1].starts[0] === 'dQw4w9WgXcQ',
    'changed list must replace the current video');
  g.pagehide();
  check(g.intervals.size === 0 && g.players[1].stops === 1,
    'pagehide must clean timers and active player');

  const dstWindow = {id: 'dst', name: 'dst', days: [6], start: '01:00',
    end: '02:00', videos: [{title: 'DST', video_id: 'M7lc1UVf-VE'}]};
  const fallFirst = await scenario([dstWindow], '2026-11-01T01:30:00-04:00');
  const fallSecond = await scenario([dstWindow], '2026-11-01T01:30:00-05:00');
  check(fallFirst.players.length === 1 && fallSecond.players.length === 1,
    'both repeated fall-back hours must use local civil time');
  return 'scheduled player harness passed';
}
runDisplayPlayerHarness;
