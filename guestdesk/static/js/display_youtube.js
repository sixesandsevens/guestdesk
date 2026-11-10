/* Reusable YouTube playback component for device testing and scheduled mode. */
(() => {
  const API_TIMEOUT_MS = 12000;
  const READY_TIMEOUT_MS = 15000;
  const START_TIMEOUT_MS = 15000;
  const BUFFER_TIMEOUT_MS = 20000;
  let apiPromise = null;

  function loadAPI() {
    if (window.YT && window.YT.Player) return Promise.resolve(window.YT);
    if (apiPromise) return apiPromise;
    apiPromise = new Promise((resolve, reject) => {
      let settled = false;
      const script = document.createElement('script');
      const previous = window.onYouTubeIframeAPIReady;
      const finish = (error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        if (window.onYouTubeIframeAPIReady === callback)
          window.onYouTubeIframeAPIReady = previous;
        if (error) { script.remove?.(); apiPromise = null; reject(error); }
        else resolve(window.YT);
      };
      const callback = () => {
        if (typeof previous === 'function') previous();
        finish(null);
      };
      window.onYouTubeIframeAPIReady = callback;
      script.src = 'https://www.youtube.com/iframe_api';
      script.onerror = () => finish(new Error('YouTube API could not load'));
      const timer = setTimeout(() => finish(new Error('YouTube API load timed out')), API_TIMEOUT_MS);
      document.head.appendChild(script);
    });
    return apiPromise;
  }

  class GuestDeskYouTubePlayer {
    constructor(host, callbacks = {}, options = {}) {
      this.host = host;
      this.callbacks = callbacks;
      this.controls = options.controls === 1 ? 1 : 0;
      this.audioEnabled = options.audioEnabled === true;
      this.volume = Number.isInteger(options.volume) && options.volume >= 1 &&
        options.volume <= 100 ? options.volume : 50;
      this.player = null;
      this.timer = null;
      this.generation = 0;
      this.playing = false;
    }

    emit(values) {
      if (this.callbacks.onStatus) this.callbacks.onStatus(values);
    }

    clearTimer() {
      if (this.timer !== null) clearTimeout(this.timer);
      this.timer = null;
    }

    armTimeout(ms, label, generation) {
      this.clearTimer();
      this.timer = setTimeout(() => {
        if (generation === this.generation) this.fail(label);
      }, ms);
    }

    stop() {
      this.generation++;
      this.clearTimer();
      this.playing = false;
      const player = this.player;
      this.player = null;
      if (player) {
        try { player.stopVideo(); } catch (_) { /* iframe may have failed to load */ }
        try { player.destroy(); } catch (_) { /* already destroyed */ }
      }
      this.host.replaceChildren();
    }

    fail(reason) {
      this.emit({ready: 'unavailable', state: 'unavailable', muted: 'unknown', error: reason});
      this.stop();
      if (this.callbacks.onFailure) this.callbacks.onFailure(reason);
    }

    async start(videoId) {
      this.stop();
      const generation = this.generation;
      this.emit({ready: 'loading', state: 'starting', muted: 'unknown', error: 'none'});
      this.armTimeout(API_TIMEOUT_MS + 1000, 'YouTube API did not become ready', generation);
      let YT;
      try { YT = await loadAPI(); }
      catch (error) {
        if (generation === this.generation) this.fail(error.message);
        return;
      }
      if (generation !== this.generation) return;
      const mount = document.createElement('div');
      this.host.appendChild(mount);
      this.armTimeout(READY_TIMEOUT_MS, 'YouTube player readiness timed out', generation);
      try {
        this.player = new YT.Player(mount, {
          width: '100%', height: '100%',
          playerVars: {
            origin: window.location.origin,
            cc_load_policy: 1, cc_lang_pref: 'en', playsinline: 1,
            controls: this.controls
          },
          events: {
            onReady: event => {
              if (generation !== this.generation) return;
              this.emit({ready: 'ready'});
              this.clearTimer();
              const iframe = event.target.getIframe();
              iframe.setAttribute('allow', 'autoplay; encrypted-media; picture-in-picture');
              try {
                event.target.setVolume(this.audioEnabled ? this.volume : 0);
                if (this.audioEnabled) event.target.unMute();
                else event.target.mute();
                if (event.target.isMuted() === this.audioEnabled) {
                  this.fail(this.audioEnabled ? 'Could not enable video sound' :
                    'Could not confirm that YouTube playback is muted');
                  return;
                }
                this.emit({muted: this.audioEnabled ? 'no' : 'yes'});
                event.target.loadVideoById({videoId, startSeconds: 0});
                event.target.playVideo();
                this.armTimeout(START_TIMEOUT_MS, 'Playback did not start (autoplay may be blocked)', generation);
              } catch (error) { this.fail(`Playback request failed: ${error.message}`); }
            },
            onStateChange: event => {
              if (generation !== this.generation) return;
              const states = YT.PlayerState;
              if (event.data === states.PLAYING) {
                this.playing = true;
                this.clearTimer();
                try {
                  if (this.audioEnabled) {
                    if (event.target.isMuted()) {
                      this.fail('YouTube playback started muted despite sound being enabled');
                      return;
                    }
                    event.target.setVolume(this.volume);
                  } else {
                    if (!event.target.isMuted()) event.target.mute();
                    if (!event.target.isMuted()) {
                      this.fail('Could not confirm that YouTube playback is muted');
                      return;
                    }
                  }
                  this.emit({state: 'playing', muted: this.audioEnabled ? 'no' : 'yes'});
                } catch (error) { this.fail(`Mute check failed: ${error.message}`); }
              } else if (event.data === states.BUFFERING) {
                this.emit({state: 'buffering'});
                this.armTimeout(BUFFER_TIMEOUT_MS, 'Playback stalled while buffering', generation);
              } else if (event.data === states.ENDED) {
                this.emit({state: 'ended'});
                this.stop();
                if (this.callbacks.onEnded) this.callbacks.onEnded();
              } else if (event.data === states.PAUSED) {
                this.emit({state: 'paused'});
                if (this.playing) this.armTimeout(BUFFER_TIMEOUT_MS, 'Playback remained paused', generation);
              } else if (event.data === states.CUED) this.emit({state: 'cued'});
            },
            onError: event => {
              if (generation !== this.generation) return;
              const reasons = {
                2: 'Invalid YouTube video ID',
                5: 'YouTube HTML5 playback failed',
                100: 'YouTube video is private or unavailable',
                101: 'Video owner does not allow embedding',
                150: 'Video owner does not allow embedding',
                153: 'YouTube did not receive the required referrer'
              };
              this.fail(`${reasons[event.data] || 'YouTube player error'} (${event.data})`);
            },
            onAutoplayBlocked: () => {
              if (generation === this.generation)
                this.fail(this.audioEnabled ? 'Autoplay blocked by browser with sound enabled' :
                  'Autoplay blocked by browser');
            }
          }
        });
      } catch (error) { this.fail(`Player creation failed: ${error.message}`); }
    }
  }

  window.GuestDeskYouTubePlayer = GuestDeskYouTubePlayer;
})();
