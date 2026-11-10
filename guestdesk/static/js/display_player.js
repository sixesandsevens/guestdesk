/* GuestDesk full-screen player. One rotation timer, one poll timer, one frame timer. */
(() => {
  const root = document.getElementById('slide-container');
  const api = window.GUESTDESK_PLAYER;
  let slides = [], metadata = {}, current = null, currentIndex = -1;
  let rotationTimer = null, frameTimer = null, fetching = false;
  let serverEpoch = Date.now(), anchor = performance.now();
  let cachedSchedule = null;
  const CACHE_MAX_AGE = 15 * 60 * 1000;
  let lastMidnightRetry = 0;
  let sequence = 0;
  let suspended = false;

  const keyOf = s => s.key || `slide:${s.id}`;
  const now = () => new Date(serverEpoch + performance.now() - anchor);
  const timezone = () => metadata.timezone || 'America/New_York';
  const fmt = options => new Intl.DateTimeFormat('en-US', {timeZone: timezone(), ...options});
  const timeRange = (start, end) => {
    const formatter = fmt({hour: 'numeric', minute: '2-digit', hour12: true});
    const first = formatter.format(start), last = formatter.format(end);
    const period = date => {
      const part = formatter.formatToParts(date).find(item => item.type === 'dayPeriod');
      return part ? part.value : '';
    };
    return period(start) === period(end)
      ? `${first.replace(/\s(?:AM|PM)$/, '')}–${last}`
      : `${first}–${last}`;
  };
  const dayKey = () => {
    const parts = fmt({year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(now());
    const value = type => parts.find(part => part.type === type).value;
    return `${value('year')}-${value('month')}-${value('day')}`;
  };
  const el = (tag, cls, value) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (value !== undefined) node.textContent = value;
    return node;
  };

  function stopFrameTimer() {
    if (frameTimer !== null) clearInterval(frameTimer);
    frameTimer = null;
  }

  function message(text) {
    sequence++;
    stopFrameTimer();
    root.replaceChildren(el('p', 'display-empty', text));
    current = null;
    currentIndex = -1;
  }

  function emptyMessage() {
    if (metadata.schedule_state === 'empty') return 'No services scheduled today';
    if (metadata.schedule_state === 'unavailable' || !Object.keys(metadata).length)
      return 'Services temporarily unavailable';
    return 'No slides configured for this display.';
  }

  function showRefreshNotice(stale) {
    const schedule = root.querySelector('.slide-frame:last-child .builtin-schedule');
    if (!schedule) return;
    let notice = schedule.querySelector('.schedule-stale');
    if (stale && !notice) schedule.appendChild(el('div', 'schedule-stale', 'Unable to refresh services temporarily'));
    else if (!stale && notice) notice.remove();
  }

  function expireOldSchedule() {
    if (!metadata.local_date || dayKey() === metadata.local_date) return;
    cachedSchedule = null;
    slides = slides.filter(slide => slide.type !== 'schedule');
    if (current && current.startsWith('builtin:schedule:')) {
      current = null;
      currentIndex = -1;
      if (rotationTimer !== null) clearTimeout(rotationTimer);
      rotationTimer = null;
      planNext();
    }
    if (performance.now() - lastMidnightRetry > 15000) {
      lastMidnightRetry = performance.now();
      refresh();
    }
  }

  function clockFrame(frame) {
    const wrap = el('div', 'builtin-clock');
    const day = el('div', 'clock-day');
    const time = el('div', 'clock-time');
    const date = el('div', 'clock-date');
    wrap.append(day, time, date);
    frame.appendChild(wrap);
    const update = () => {
      const at = now();
      day.textContent = fmt({weekday: 'long'}).format(at);
      time.textContent = fmt({hour: 'numeric', minute: '2-digit', hour12: true}).format(at);
      date.textContent = fmt({month: 'long', day: 'numeric', year: 'numeric'}).format(at);
      expireOldSchedule();
    };
    update();
    frameTimer = setInterval(update, 1000);
  }

  function scheduleFrame(frame, slide) {
    const wrap = el('div', 'builtin-schedule');
    const header = el('header', 'schedule-header');
    header.append(el('h1', '', "Today's Services"),
      el('div', 'schedule-date', fmt({weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'}).format(now())));
    wrap.appendChild(header);
    const list = el('div', 'schedule-list');
    const statuses = [];
    for (const event of slide.events) {
      const card = el('article', 'schedule-card');
      const timing = el('div', 'service-time');
      const start = new Date(event.start), end = new Date(event.end);
      timing.textContent = timeRange(start, end);
      const details = el('div', 'service-details');
      details.appendChild(el('h2', 'service-name', event.title));
      if (event.location) details.appendChild(el('p', 'service-location', event.location));
      const status = el('span', 'service-status');
      card.append(timing, details, status);
      statuses.push({card, status, start: start.getTime(), end: end.getTime()});
      list.appendChild(card);
    }
    wrap.appendChild(list);
    if (slide.pages > 1) wrap.appendChild(el('div', 'schedule-page', `Page ${slide.page} of ${slide.pages}`));
    if (slide.stale) wrap.appendChild(el('div', 'schedule-stale', 'Unable to refresh services temporarily'));
    frame.appendChild(wrap);
    const update = () => {
      const stamp = now().getTime();
      for (const item of statuses) {
        const done = stamp >= item.end;
        const active = stamp >= item.start && !done;
        item.card.classList.toggle('completed', done);
        item.card.classList.toggle('happening', active);
        item.status.textContent = active ? 'Happening now' : '';
      }
      header.querySelector('.schedule-date').textContent = fmt({weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'}).format(now());
      expireOldSchedule();
    };
    update();
    frameTimer = setInterval(update, 1000);
  }

  function buildFrame(slide) {
    const frame = el('div', 'slide-frame');
    if (slide.type === 'clock') clockFrame(frame);
    else if (slide.type === 'schedule') scheduleFrame(frame, slide);
    else if (slide.type === 'image' && slide.file_url) {
      const image = el('img', 'display-slide-image'); image.src = slide.file_url; frame.appendChild(image);
    } else if (slide.type === 'video' && slide.file_url) {
      const video = el('video', 'display-slide-video');
      video.src = slide.file_url; video.autoplay = true; video.loop = true;
      video.muted = true; video.playsInline = true; video.controls = false;
      frame.appendChild(video);
    } else if (slide.type === 'text') {
      const wrap = el('div', 'display-slide-text');
      if (slide.headline) wrap.appendChild(el('h1', '', slide.headline));
      if (slide.subheadline) wrap.appendChild(el('h2', '', slide.subheadline));
      if (slide.body) wrap.appendChild(el('p', '', slide.body));
      frame.appendChild(wrap);
    }
    return frame;
  }

  function mediaReady(frame) {
    const media = frame.querySelector('img, video');
    if (!media || (media.tagName === 'IMG' && media.complete) ||
        (media.tagName === 'VIDEO' && media.readyState >= 2)) return Promise.resolve();
    return new Promise(resolve => {
      const done = () => { clearTimeout(timeout); resolve(); };
      media.addEventListener('load', done, {once: true});
      media.addEventListener('loadeddata', done, {once: true});
      media.addEventListener('error', done, {once: true});
      const timeout = setTimeout(resolve, 3000);
    });
  }

  async function show(slide, index) {
    const token = ++sequence;
    stopFrameTimer();
    const frame = buildFrame(slide);
    await mediaReady(frame);
    if (token !== sequence) return;
    const frames = root.querySelectorAll('.slide-frame');
    const old = frames.length ? frames[frames.length - 1] : null;
    frames.forEach(node => { if (node !== old) node.remove(); });
    root.appendChild(frame);
    const video = frame.querySelector('video');
    if (video) {
      try { video.currentTime = 0; } catch (_) { /* media may still be loading */ }
      video.play().catch(() => {});
    }
    if (old && slide.transition === 'fade') {
      frame.classList.add('fade-frame-enter');
      old.classList.add('fade-frame-exit');
      const remove = () => { old.remove(); frame.classList.remove('fade-frame-enter'); };
      old.addEventListener('animationend', remove, {once: true});
      setTimeout(remove, 3000);
    } else if (old) old.remove();
    current = keyOf(slide);
    currentIndex = index;
  }

  function planNext() {
    if (rotationTimer !== null) clearTimeout(rotationTimer);
    rotationTimer = null;
    if (suspended) return;
    if (!slides.length) { message(emptyMessage()); return; }
    let index = slides.findIndex(s => keyOf(s) === current);
    if (index < 0) index = Math.min(currentIndex, slides.length - 1);
    const next = current === null ? 0 : (index + 1) % slides.length;
    const slide = slides[next];
    if (keyOf(slide) !== current || slide.type === 'schedule') show(slide, next);
    else currentIndex = next; // A lone clock stays mounted and keeps ticking.
    rotationTimer = setTimeout(planNext, (api.previewDuration || slide.duration || 10) * 1000);
  }

  async function refresh() {
    if (fetching) return;
    fetching = true;
    try {
      const response = await fetch(api.url, {cache: 'no-store'});
      if (response.status === 404) {
        slides = [];
        cachedSchedule = null;
        metadata = {schedule_state: 'disabled'};
        if (rotationTimer !== null) clearTimeout(rotationTimer);
        rotationTimer = null;
        message('This display is unavailable.');
        return;
      }
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      serverEpoch = Date.parse(data.server_time || new Date().toISOString());
      anchor = performance.now();
      metadata = data;
      let incoming = data.slides || [];
      if (data.schedule_state === 'ok') {
        cachedSchedule = {date: data.local_date, at: performance.now(),
          pages: incoming.filter(s => s.type === 'schedule')};
      } else if (data.schedule_state === 'empty') cachedSchedule = null;
      else if (data.schedule_state === 'unavailable' && cachedSchedule &&
               cachedSchedule.date === data.local_date &&
               performance.now() - cachedSchedule.at < CACHE_MAX_AGE) {
        const manual = incoming.filter(s => s.type !== 'schedule');
        const clockCount = manual.length && manual[0].type === 'clock' ? 1 : 0;
        incoming = [...manual.slice(0, clockCount), ...cachedSchedule.pages.map(s => ({...s, stale: true})), ...manual.slice(clockCount)];
      }
      slides = incoming;
      showRefreshNotice(data.schedule_state === 'unavailable');
      if (rotationTimer === null && !suspended) planNext();
    } catch (error) {
      // Keep existing clock and manual slides. Never carry schedule into a new day.
      if (cachedSchedule && metadata.local_date &&
          (dayKey() !== metadata.local_date || performance.now() - cachedSchedule.at >= CACHE_MAX_AGE)) {
        cachedSchedule = null;
        slides = slides.filter(s => s.type !== 'schedule');
        metadata.schedule_state = 'unavailable';
      } else slides = slides.map(s => s.type === 'schedule' ? {...s, stale: true} : s);
      showRefreshNotice(true);
      if (rotationTimer === null && !suspended) planNext();
    } finally { fetching = false; }
  }

  window.GuestDeskSlideshow = {
    suspend() {
      suspended = true;
      if (rotationTimer !== null) clearTimeout(rotationTimer);
      rotationTimer = null;
      root.querySelectorAll('video').forEach(video => video.pause());
    },
    resume() {
      if (!suspended) return;
      suspended = false;
      planNext();
    }
  };

  refresh();
  setInterval(refresh, 60000);
})();
