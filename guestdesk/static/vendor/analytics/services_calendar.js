/* global FullCalendar, bootstrap */
document.addEventListener('DOMContentLoaded', () => {
  initCalendarPage().catch((err) => console.error('Calendar init failed', err));
});

let calendar;
let serviceMap = {};
let urlParams;
let sidParam;
let previewDebounce;

async function initCalendarPage() {
  urlParams = new URLSearchParams(window.location.search);
  sidParam = urlParams.get('service_id');

  await loadServices();
  initServiceChangeHandler();
  initAddScheduleButton();
  initSaveHandler();
  initSeriesTableHandlers();
  initCalendar();
  await renderSeriesTable();
  schedulePreviewRefresh();
}

function getCsrfToken() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute('content') : '';
}

function showToast(message, type = 'success') {
  const holder = document.getElementById('toast-holder');
  if (!holder) {
    alert(message);
    return;
  }
  const id = `toast-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const bg = {
    success: 'bg-success text-white',
    danger: 'bg-danger text-white',
    info: 'bg-info text-dark',
    warning: 'bg-warning text-dark'
  }[type] || 'bg-secondary text-white';
  const el = document.createElement('div');
  el.className = `toast ${bg}`;
  el.id = id;
  el.role = 'alert';
  el.ariaLive = 'assertive';
  el.ariaAtomic = 'true';
  el.innerHTML = `
    <div class="d-flex">
      <div class="toast-body">${message}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast" aria-label="Close"></button>
    </div>`;
  holder.appendChild(el);
  const t = new bootstrap.Toast(el, { delay: 2400 });
  t.show();
  el.addEventListener('hidden.bs.toast', () => el.remove());
}

async function loadServices(force = false) {
  const sel = document.getElementById('sched-service');
  if (!sel) return;
  if (Object.keys(serviceMap).length && !force) {
    if (sidParam) {
      sel.value = String(parseInt(sidParam, 10));
      sel.disabled = true;
    }
    return;
  }
  try {
    const res = await fetch('/admin/services/options');
    const items = await res.json();
    serviceMap = {};
    sel.innerHTML = '<option value="">Select a service…</option>' +
      items.map((i) => {
        serviceMap[String(i.id)] = i.name;
        return `<option value="${i.id}">${i.name}</option>`;
      }).join('');
    if (sidParam) {
      sel.value = String(parseInt(sidParam, 10));
      sel.disabled = true;
    }
  } catch (err) {
    console.error('Failed to load services', err);
  }
}

function initServiceChangeHandler() {
  const sel = document.getElementById('sched-service');
  if (!sel) return;
  sel.addEventListener('change', async () => {
    if (sidParam) return; // locked when scoped via URL
    await renderSeriesTable();
    calendar?.refetchEvents();
  });
}

function initAddScheduleButton() {
  const btn = document.getElementById('new-series');
  if (!btn) return;
  btn.addEventListener('click', () => {
    resetModalFields();
    const modal = new bootstrap.Modal(document.getElementById('rruleModal'));
    modal.show();
  });
}

function resetModalFields() {
  const modalEl = document.getElementById('rruleModal');
  if (!modalEl) return;
  modalEl.removeAttribute('data-edit-id');

  const sel = document.getElementById('sched-service');
  if (sel) {
    if (sidParam) {
      sel.value = String(parseInt(sidParam, 10));
      sel.disabled = true;
    } else {
      sel.disabled = false;
      sel.value = '';
    }
  }

  const titleInput = document.getElementById('sched-title');
  if (titleInput) {
    titleInput.value = getSelectedServiceName();
  }

  document.getElementById('sched-date').value = '';
  document.getElementById('sched-start').value = '10:00';
  document.getElementById('sched-end').value = '12:00';
  document.getElementById('bysetpos').checked = true;
  document.getElementById('bymonthday').checked = false;
  document.getElementById('mn-dom').value = '1';
  document.getElementById('mn-dow').value = 'MO';
  document.querySelectorAll('.dow').forEach((cb) => { cb.checked = false; });
  document.querySelectorAll('.mn-pos').forEach((cb) => { cb.checked = false; });
  document.querySelectorAll('.qp').forEach((btn) => btn.classList.remove('active'));
  document.querySelector('[data-bs-target="#weeklyTab"]').click();
  setSummaryAndRRule();
  schedulePreviewRefresh();
}

function getSelectedServiceId() {
  if (sidParam) return sidParam;
  const sel = document.getElementById('sched-service');
  return sel ? sel.value : '';
}

function getSelectedServiceName() {
  const sid = getSelectedServiceId();
  if (!sid) return '';
  return serviceMap[sid] || '';
}

function initSaveHandler() {
  const saveBtn = document.getElementById('sched-save');
  if (!saveBtn) return;
  saveBtn.addEventListener('click', async () => {
    const titleInput = document.getElementById('sched-title');
    const date = document.getElementById('sched-date').value;
    const st = document.getElementById('sched-start').value;
    const et = document.getElementById('sched-end').value;
    const rrule = document.getElementById('sched-rrule').value;
    const serviceId = getSelectedServiceId();

    if (!serviceId) { showToast('Please choose a service.', 'warning'); return; }
    if (!date || !st || !et) { showToast('Please choose date & time.', 'warning'); return; }

    const serviceName = getSelectedServiceName();
    const title = (titleInput?.value || '').trim() || serviceName || 'Untitled Service';

    const payload = {
      title,
      dtstart: `${date}T${st}:00`,
      dtend: `${date}T${et}:00`,
      rrule,
      rdate: null,
      exdate: null,
      service_id: parseInt(serviceId, 10),
    };

    const modalEl = document.getElementById('rruleModal');
    const editId = modalEl.getAttribute('data-edit-id');
    const method = editId ? 'PUT' : 'POST';
    const url = editId ? `/admin/services/series/${editId}` : '/admin/services/series';

    saveBtn.disabled = true;
    try {
      const res = await fetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
        },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const text = await res.text();
        throw new Error(text);
      }
    } catch (err) {
      console.error('Failed to save schedule', err);
      showToast('Failed to save schedule.', 'danger');
      return;
    } finally {
      saveBtn.disabled = false;
    }

    bootstrap.Modal.getInstance(modalEl)?.hide();
    showToast(editId ? 'Schedule updated.' : 'Schedule saved.', 'success');
    await renderSeriesTable();
    calendar?.refetchEvents();
  });
}

function initSeriesTableHandlers() {
  document.addEventListener('click', async (ev) => {
    const target = ev.target;
    if (!(target instanceof HTMLElement)) return;
    const row = target.closest('tr[data-id]');
    if (!row) return;
    const id = row.getAttribute('data-id');

    if (target.classList.contains('act-del')) {
      if (!confirm('Delete this schedule?')) return;
      try {
        const res = await fetch(`/admin/services/series/${id}`, {
          method: 'DELETE',
          headers: { 'X-CSRFToken': getCsrfToken() },
        });
        if (!res.ok) throw new Error(await res.text());
        showToast('Schedule deleted.', 'success');
      } catch (err) {
        console.error('Delete failed', err);
        showToast('Failed to delete schedule.', 'danger');
        return;
      }
      await renderSeriesTable();
      calendar?.refetchEvents();
      return;
    }

    if (target.classList.contains('act-edit')) {
      await openEditModal(id);
    }
  });
}

async function fetchSeriesForService(serviceId) {
  const res = await fetch(`/admin/services/series?service_id=${serviceId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function renderSeriesTable() {
  const body = document.querySelector('#series-table tbody');
  if (!body) return;
  const serviceId = getSelectedServiceId();
  if (!serviceId) {
    body.innerHTML = '<tr><td colspan="5" class="text-muted p-3">Select a service to see its schedules.</td></tr>';
    return;
  }
  let rows = [];
  try {
    rows = await fetchSeriesForService(serviceId);
  } catch (err) {
    console.error('Failed to load series', err);
    body.innerHTML = '<tr><td colspan="5" class="text-danger p-3">Unable to load schedules.</td></tr>';
    return;
  }
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="5" class="text-muted p-3">No schedules yet.</td></tr>';
    return;
  }
  body.innerHTML = rows.map((r) => {
    const svcKey = String(r.service_id ?? '');
    const svcName = (r.service_name || serviceMap[svcKey] || '').trim();
    return `
      <tr data-id="${r.id}">
        <td>${svcName}</td>
        <td>${r.title || ''}</td>
        <td><code>${r.rrule || ''}</code></td>
        <td>${(r.dtstart || '').slice(0,16)}–${(r.dtend || '').slice(11,16)}</td>
        <td>
          <button class="btn btn-sm btn-outline-primary act-edit">Edit</button>
          <button class="btn btn-sm btn-outline-danger ms-1 act-del">Delete</button>
        </td>
      </tr>
    `;
  }).join('');
}

async function openEditModal(seriesId) {
  await loadServices(true);
  let data;
  try {
    const res = await fetch(`/admin/services/series/${seriesId}`);
    if (!res.ok) throw new Error(await res.text());
    data = await res.json();
  } catch (err) {
    console.error('Failed to fetch series detail', err);
    showToast('Could not load schedule details.', 'danger');
    return;
  }

  const modalEl = document.getElementById('rruleModal');
  modalEl.setAttribute('data-edit-id', seriesId);

  const sel = document.getElementById('sched-service');
  if (sel) {
    sel.value = data.service_id ? String(data.service_id) : '';
    if (sidParam) {
      sel.disabled = true;
    } else {
      sel.disabled = false;
    }
  }

  const titleInput = document.getElementById('sched-title');
  if (titleInput) {
    titleInput.value = data.title || getSelectedServiceName() || '';
  }

  document.getElementById('sched-date').value = (data.dtstart || '').slice(0,10);
  document.getElementById('sched-start').value = (data.dtstart || '').slice(11,16) || '10:00';
  document.getElementById('sched-end').value = (data.dtend || '').slice(11,16) || '12:00';

  document.querySelectorAll('.dow').forEach((cb) => { cb.checked = false; });
  document.querySelectorAll('.mn-pos').forEach((cb) => { cb.checked = false; });
  document.querySelectorAll('.qp').forEach((btn) => btn.classList.remove('active'));

  const r = (data.rrule || '').split(';').reduce((acc, piece) => {
    const [k, v] = piece.split('=');
    if (k && v) acc[k] = v;
    return acc;
  }, {});

  if (r.FREQ === 'WEEKLY') {
    document.querySelector('[data-bs-target="#weeklyTab"]').click();
    document.getElementById('wk-interval').value = r.INTERVAL || '1';
    const days = (r.BYDAY || '').split(',');
    document.querySelectorAll('.dow').forEach((cb) => {
      cb.checked = days.includes(cb.value);
    });
  } else if (r.FREQ === 'MONTHLY') {
    document.querySelector('[data-bs-target="#monthlyTab"]').click();
    if (r.BYSETPOS && r.BYDAY) {
      document.getElementById('bysetpos').checked = true;
      const poss = (r.BYSETPOS || '').split(',');
      document.querySelectorAll('.mn-pos').forEach((cb) => {
        cb.checked = poss.includes(cb.value);
      });
      document.getElementById('mn-dow').value = r.BYDAY;
    } else if (r.BYMONTHDAY) {
      document.getElementById('bymonthday').checked = true;
      document.getElementById('mn-dom').value = r.BYMONTHDAY;
    }
  } else {
    document.querySelector('[data-bs-target="#oneoffTab"]').click();
  }

  setSummaryAndRRule();
  schedulePreviewRefresh();
  new bootstrap.Modal(modalEl).show();
}

function initCalendar() {
  const el = document.getElementById('calendar');
  if (!el || typeof FullCalendar === 'undefined') return;
  if (calendar) {
    calendar.refetchEvents();
    return;
  }
  calendar = new FullCalendar.Calendar(el, {
    initialView: 'dayGridMonth',
    height: 'auto',
    headerToolbar: {
      left: 'prev,next today',
      center: 'title',
      right: 'dayGridMonth,timeGridWeek,timeGridDay',
    },
    events: async (info, success, failure) => {
      try {
        const params = new URLSearchParams({ start: info.startStr, end: info.endStr });
        const serviceId = getSelectedServiceId();
        if (serviceId) params.set('service_id', serviceId);
        const res = await fetch(`/admin/services/feed?${params.toString()}`);
        if (!res.ok) throw new Error(await res.text());
        const data = await res.json();
        success(data);
      } catch (err) {
        console.error('Failed to load calendar events', err);
        failure(err);
      }
    },
    eventClick: (info) => {
      const seriesId = info.event.extendedProps.series_id;
      const serviceId = info.event.extendedProps.service_id;
      const source = info.event.extendedProps.source;
      if (source === 'slot' && serviceId) {
        window.location.href = `/admin/services/${serviceId}/slots`;
        return;
      }
      if (seriesId) {
        openEditModal(seriesId);
      }
    },
    eventDrop: async (info) => {
      try {
        await postOverride({
          series_id: info.event.extendedProps.series_id,
          service_id: info.event.extendedProps.service_id,
          instance_start: info.event.extendedProps.instance_start,
          new_dtstart: info.event.start?.toISOString(),
          new_dtend: info.event.end?.toISOString(),
        });
        calendar.refetchEvents();
      } catch (err) {
        console.error('Failed to update event', err);
        info.revert();
        showToast('Could not move event.', 'danger');
      }
    },
    eventResize: async (info) => {
      try {
        await postOverride({
          series_id: info.event.extendedProps.series_id,
          service_id: info.event.extendedProps.service_id,
          instance_start: info.event.extendedProps.instance_start,
          new_dtstart: info.event.start?.toISOString(),
          new_dtend: info.event.end?.toISOString(),
        });
        calendar.refetchEvents();
      } catch (err) {
        console.error('Failed to resize event', err);
        info.revert();
        showToast('Could not resize event.', 'danger');
      }
    },
  });
  calendar.render();
}

async function postOverride(payload) {
  if (!payload.series_id) return;
  const body = JSON.stringify(payload);
  const res = await fetch('/admin/services/override', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCsrfToken(),
    },
    body,
  });
  if (!res.ok) throw new Error(await res.text());
}

// --- Summary + RRULE helpers (existing logic) ---
function setSummaryAndRRule() {
  const date = document.getElementById('sched-date')?.value || '';
  const st = document.getElementById('sched-start')?.value || '';
  const et = document.getElementById('sched-end')?.value || '';
  const weeklyActive = document.getElementById('weeklyTab')?.classList.contains('active');
  const monthlyActive = document.getElementById('monthlyTab')?.classList.contains('active');
  const oneoffActive = document.getElementById('oneoffTab')?.classList.contains('active');
  let rrule = '';
  let summary = '';

  if (weeklyActive) {
    const interval = Math.max(1, parseInt(document.getElementById('wk-interval').value || '1', 10));
    const dows = Array.from(document.querySelectorAll('.dow:checked')).map((el) => el.value);
    if (dows.length > 0) {
      rrule = `FREQ=WEEKLY;INTERVAL=${interval};BYDAY=${dows.join(',')}`;
      const names = { MO: 'Mon', TU: 'Tue', WE: 'Wed', TH: 'Thu', FR: 'Fri', SA: 'Sat', SU: 'Sun' };
      summary = (interval === 1 ? 'Every ' : `Every ${interval} weeks on `) + dows.map((d) => names[d]).join(', ');
    }
  } else if (monthlyActive) {
    const mode = document.querySelector('input[name="monthMode"]:checked').value;
    if (mode === 'bysetpos') {
      const pos = Array.from(document.querySelectorAll('.mn-pos:checked')).map((el) => el.value);
      const dow = document.getElementById('mn-dow').value;
      if (pos.length > 0) {
        const posLabelMap = { '1': '1st', '2': '2nd', '3': '3rd', '4': '4th', '-1': 'Last' };
        const names = { MO: 'Monday', TU: 'Tuesday', WE: 'Wednesday', TH: 'Thursday', FR: 'Friday', SA: 'Saturday', SU: 'Sunday' };
        rrule = `FREQ=MONTHLY;BYDAY=${dow};BYSETPOS=${pos.join(',')}`;
        const posText = pos.map((p) => posLabelMap[p] || p).join(' & ');
        summary = `Every month on the ${posText} ${names[dow]}`;
      }
    } else {
      const dom = Math.max(1, Math.min(31, parseInt(document.getElementById('mn-dom').value || '1', 10)));
      rrule = `FREQ=MONTHLY;BYMONTHDAY=${dom}`;
      summary = `Every month on day ${dom}`;
    }
  } else if (oneoffActive) {
    rrule = '';
    summary = 'One-off on selected date';
  }

  const rruleInput = document.getElementById('sched-rrule');
  if (rruleInput) rruleInput.value = rrule;
  const sumEl = document.getElementById('sched-summary');
  if (sumEl) {
    if (summary) {
      sumEl.textContent = (date && st && et) ? `${summary} at ${st}–${et} starting ${date}` : summary;
    } else {
      sumEl.textContent = 'Fill in details…';
    }
  }
  schedulePreviewRefresh();
}

['sched-date','sched-start','sched-end','wk-interval','mn-dow','mn-dom'].forEach((id) => {
  const el = document.getElementById(id);
  if (el) el.addEventListener('input', setSummaryAndRRule);
});
document.querySelectorAll('.dow, input[name="monthMode"]').forEach((el) => el.addEventListener('change', setSummaryAndRRule));
document.querySelectorAll('.mn-pos').forEach((el) => el.addEventListener('change', setSummaryAndRRule));
document.querySelectorAll('#freqTabs .nav-link').forEach((el) => el.addEventListener('shown.bs.tab', setSummaryAndRRule));

// Quick pick shortcuts
document.querySelectorAll('.qp').forEach((btn) => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.qp').forEach((b) => b.classList.remove('active'));
    btn.classList.add('active');

    const code = btn.dataset.qp;
    document.getElementById('wk-interval').value = 1;
    document.querySelectorAll('.dow').forEach((cb) => { cb.checked = false; });
    document.querySelectorAll('.mn-pos').forEach((cb) => { cb.checked = false; });

    if (code === 'WEEKDAYS') {
      ['MO','TU','WE','TH','FR'].forEach((v) => document.querySelector(`.dow[value="${v}"]`).checked = true);
      document.querySelector('[data-bs-target="#weeklyTab"]').click();
    } else if (code === 'WEEKENDS') {
      ['SA','SU'].forEach((v) => document.querySelector(`.dow[value="${v}"]`).checked = true);
      document.querySelector('[data-bs-target="#weeklyTab"]').click();
    } else if (code === 'EVERY_OTHER_TU') {
      document.getElementById('wk-interval').value = 2;
      document.querySelector(`.dow[value="TU"]`).checked = true;
      document.querySelector('[data-bs-target="#weeklyTab"]').click();
    } else if (code === 'SECOND_FOURTH_MO') {
      document.querySelector('[data-bs-target="#monthlyTab"]').click();
      document.getElementById('bysetpos').checked = true;
      document.querySelector('.mn-pos[value="2"]').checked = true;
      document.querySelector('.mn-pos[value="4"]').checked = true;
      document.getElementById('mn-dow').value = 'MO';
    } else if (code === 'LAST_FR') {
      document.querySelector('[data-bs-target="#monthlyTab"]').click();
      document.getElementById('bysetpos').checked = true;
      document.querySelector('.mn-pos[value="-1"]').checked = true;
      document.getElementById('mn-dow').value = 'FR';
    }
    setSummaryAndRRule();
  });
});

document.getElementById('rruleModal')?.addEventListener('shown.bs.modal', () => {
  schedulePreviewRefresh();
});

function schedulePreviewRefresh() {
  clearTimeout(previewDebounce);
  previewDebounce = setTimeout(refreshPreview, 150);
}

async function refreshPreview() {
  const list = document.getElementById('sched-preview-list');
  const spinner = document.getElementById('sched-preview-spinner');
  if (!list || !spinner) return;

  const date = document.getElementById('sched-date')?.value;
  const st = document.getElementById('sched-start')?.value;
  const rrule = document.getElementById('sched-rrule')?.value || '';

  list.innerHTML = '';
  if (!date || !st) {
    spinner.style.display = 'none';
    return;
  }

  spinner.style.display = '';
  try {
    const qs = new URLSearchParams({ dtstart: `${date}T${st}:00`, rrule, tz: 'America/New_York', n: '6' });
    const res = await fetch(`/admin/services/preview?${qs.toString()}`);
    const json = await res.json();
    if (!json.ok) throw new Error(json.error || 'preview_failed');

    const formatter = new Intl.DateTimeFormat(undefined, {
      weekday: 'short', year: 'numeric', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit'
    });

    list.innerHTML = json.dates.map((d) => `<li>${formatter.format(new Date(d))}</li>`).join('');
  } catch (err) {
    console.error('Preview failed', err);
    list.innerHTML = '<li class="text-danger">Preview failed.</li>';
  } finally {
    spinner.style.display = 'none';
  }
}
