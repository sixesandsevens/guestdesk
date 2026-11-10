/* The test uses the same component intended for scheduled display playback. */
(() => {
  const stage = document.getElementById('test-stage');
  const signage = stage.querySelector('iframe');
  const slideshow = action => {
    try { signage.contentWindow.GuestDeskSlideshow?.[action](); }
    catch (_) { /* the iframe may still be loading */ }
  };
  signage.addEventListener('load', () => {
    if (stage.classList.contains('video-starting') || stage.classList.contains('video-playing'))
      slideshow('suspend');
  });
  const labels = {
    ready: document.getElementById('diag-ready'),
    state: document.getElementById('diag-state'),
    muted: document.getElementById('diag-muted'),
    error: document.getElementById('diag-error')
  };
  const player = new window.GuestDeskYouTubePlayer(
    document.getElementById('youtube-player'), {
      onStatus(values) {
        for (const [key, value] of Object.entries(values)) labels[key].textContent = value;
        if (values.state === 'starting') {
          stage.classList.add('video-starting');
          slideshow('suspend');
        }
        if (values.state === 'playing') {
          stage.classList.remove('video-starting');
          stage.classList.add('video-playing');
        }
      },
      onFailure() {
        stage.classList.remove('video-starting', 'video-playing');
        slideshow('resume');
      },
      onEnded() {
        stage.classList.remove('video-starting', 'video-playing');
        slideshow('resume');
      }
    }, {controls: 1}
  );
  player.start(window.GUESTDESK_YOUTUBE_TEST.videoId);
  window.addEventListener('pagehide', () => player.stop(), {once: true});
})();
