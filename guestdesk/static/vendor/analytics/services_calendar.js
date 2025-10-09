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
  initAdditionalTimeControls();
  initSaveHandler();
  initDeleteHandler();
  initSeriesTableHandlers();
  initCalendar();
  await renderSeriesTable();
  schedulePreviewRefresh();
}

function initAdditionalTimeControls() {
  const addBtn = document.getElementById('sched-time-add');
  if (addBtn) {
    addBtn.addEventListener('click', () => {
      addExtraTimeRow();
      setSummaryAndRRule();
    });
  }

  const container = document.getElementById('sched-extra-times');
  if (container) {
    container.addEventListener('click', (ev) => {
      const target = ev.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.classList.contains('sched-time-remove')) {
        const row = target.closest('.sched-extra-row');
        row?.remove();
        setSummaryAndRRule();
        schedulePreviewRefresh();
      }
    });
  }
}

function clearExtraTimeRows() {
  const container = document.getElementById('sched-extra-times');
  if (container) {
    container.innerHTML = '';
  }
}

function addExtraTimeRow(start = '', end = '') {
  const container = document.getElementById('sched-extra-times');
  if (!container) return;
  const row = document.createElement('div');
  row.className = 'row g-2 align-items-end sched-extra-row mt-1';
  row.innerHTML = `
    <div class="col-md-3 offset-md-3">
      <label class="form-label visually-hidden">Start time</label>
      <input type="time" class="form-control sched-extra-start" value="${start}">
    </div>
    <div class="col-md-3">
      <label class="form-label visually-hidden">End time</label>
      <input type="time" class="form-control sched-extra-end" value="${end}">
    </div>
    <div class="col-md-3">
      <button type="button" class="btn btn-link text-danger sched-time-remove">Remove</button>
    </div>`;
  container.appendChild(row);
  row.querySelectorAll('input').forEach((input) => {
    input.addEventListener('input', () => {
      setSummaryAndRRule();
      schedulePreviewRefresh();
    });
  });
  schedulePreviewRefresh();
}

function getAllTimeSlots() {
  const slots = [];
  const baseStart = document.getElementById('sched-start')?.value || '';
  const baseEnd = document.getElementById('sched-end')?.value || '';
  if (baseStart || baseEnd) {
    slots.push({ start: baseStart, end: baseEnd });
  }
  document.querySelectorAll('#sched-extra-times .sched-extra-row').forEach((row) => {
    const start = row.querySelector('.sched-extra-start')?.value || '';
    const end = row.querySelector('.sched-extra-end')?.value || '';
    slots.push({ start, end });
  });
  return slots;
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

  const delBtn = document.getElementById('sched-delete');
  if (delBtn) {
    delBtn.classList.add('d-none');
    delBtn.disabled = false;
  }

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
  clearExtraTimeRows();
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
    const rrule = document.getElementById('sched-rrule').value;
    const serviceId = getSelectedServiceId();

    if (!serviceId) { showToast('Please choose a service.', 'warning'); return; }
    if (!date) { showToast('Please choose date & time.', 'warning'); return; }

    const serviceName = getSelectedServiceName();
    const title = (titleInput?.value || '').trim() || serviceName || 'Untitled Service';

    const slots = getAllTimeSlots();
    const hasPartial = slots.some((slot) => (slot.start && !slot.end) || (!slot.start && slot.end));
    if (hasPartial) { showToast('Each time needs both a start and end.', 'warning'); return; }

    const filledSlots = slots.filter((slot) => slot.start && slot.end);
    if (!filledSlots.length) { showToast('Please add at least one time window.', 'warning'); return; }

    const badRange = filledSlots.find((slot) => slot.start >= slot.end);
    if (badRange) { showToast('Each time must start before it ends.', 'warning'); return; }

    const serviceIdInt = parseInt(serviceId, 10);
    if (Number.isNaN(serviceIdInt)) { showToast('Invalid service selection.', 'danger'); return; }

    const payloadBase = {
      title,
      rrule,
      rdate: null,
      exdate: null,
      service_id: serviceIdInt,
    };

    const modalEl = document.getElementById('rruleModal');
    const editId = modalEl.getAttribute('data-edit-id');

    saveBtn.disabled = true;
    try {
      const submitSlot = async (slot, method, targetUrl) => {
        const body = JSON.stringify({
          ...payloadBase,
          dtstart: `${date}T${slot.start}:00`,
          dtend: `${date}T${slot.end}:00`,
        });
        const res = await fetch(targetUrl, {
          method,
          headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken(),
          },
          body,
        });
        if (!res.ok) {
          const text = await res.text();
          throw new Error(text || 'save_failed');
        }
      };

      if (editId) {
        const [primarySlot, ...extraSlots] = filledSlots;
        if (primarySlot) {
          await submitSlot(primarySlot, 'PUT', `/admin/services/series/${editId}`);
        }
        for (const slot of extraSlots) {
          await submitSlot(slot, 'POST', '/admin/services/series');
        }
      } else {
        for (const slot of filledSlots) {
          await submitSlot(slot, 'POST', '/admin/services/series');
        }
      }
    } catch (err) {
      console.error('Failed to save schedule', err);
      showToast('Failed to save schedule.', 'danger');
      return;
    } finally {
      saveBtn.disabled = false;
    }

    bootstrap.Modal.getInstance(modalEl)?.hide();
    const successMessage = (() => {
      if (editId) {
        return filledSlots.length > 1 ? 'Schedule updated. Extra times added.' : 'Schedule updated.';
      }
      return filledSlots.length > 1 ? 'Schedules saved.' : 'Schedule saved.';
    })();
    showToast(successMessage, 'success');
    await renderSeriesTable();
    calendar?.refetchEvents();
  });
}

function initDeleteHandler() {
  const deleteBtn = document.getElementById('sched-delete');
  if (!deleteBtn) return;
  deleteBtn.addEventListener('click', async () => {
    const modalEl = document.getElementById('rruleModal');
    const editId = modalEl?.getAttribute('data-edit-id');
    if (!editId) return;
    if (!confirm('Delete this schedule?')) return;
    deleteBtn.disabled = true;
    try {
      await deleteSeries(editId);
    } catch (err) {
      console.error('Failed to delete schedule', err);
      showToast('Failed to delete schedule.', 'danger');
      deleteBtn.disabled = false;
      return;
    }
    bootstrap.Modal.getInstance(modalEl)?.hide();
    resetModalFields();
    showToast('Schedule deleted.', 'success');
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
        await deleteSeries(id);
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
  clearExtraTimeRows();

  const delBtn = document.getElementById('sched-delete');
  if (delBtn) {
    delBtn.classList.remove('d-none');
    delBtn.disabled = false;
  }

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

async function deleteSeries(seriesId) {
  const res = await fetch(`/admin/services/series/${seriesId}`, {
    method: 'DELETE',
    headers: { 'X-CSRFToken': getCsrfToken() },
  });
  if (!res.ok) throw new Error(await res.text());
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
  const slots = getAllTimeSlots().filter((slot) => slot.start && slot.end);
  const firstSlot = slots[0] || { start: '', end: '' };
  const st = firstSlot.start;
  const et = firstSlot.end;
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
      const timeSummary = slots.map((slot) => `${slot.start}–${slot.end}`).join(', ');
      if (timeSummary) {
        if (date) {
          sumEl.textContent = `${summary} at ${timeSummary} starting ${date}`;
        } else {
          sumEl.textContent = `${summary} at ${timeSummary}`;
        }
      } else {
        sumEl.textContent = summary;
      }
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
  const slots = getAllTimeSlots().filter((slot) => slot.start);
  const rrule = document.getElementById('sched-rrule')?.value || '';

  list.innerHTML = '';
  if (!date || !slots.length) {
    spinner.style.display = 'none';
    return;
  }

  spinner.style.display = '';
  try {
    const formatter = new Intl.DateTimeFormat(undefined, {
      weekday: 'short', year: 'numeric', month: 'short', day: 'numeric',
      hour: 'numeric', minute: '2-digit'
    });

    const allDates = [];
    for (const slot of slots) {
      const qs = new URLSearchParams({ dtstart: `${date}T${slot.start}:00`, rrule, tz: 'America/New_York', n: '6' });
      const res = await fetch(`/admin/services/preview?${qs.toString()}`);
      const json = await res.json();
      if (!json.ok) throw new Error(json.error || 'preview_failed');
      json.dates.forEach((d) => {
        allDates.push({ iso: d, label: slot.end ? `${slot.start}–${slot.end}` : slot.start });
      });
    }

    allDates.sort((a, b) => new Date(a.iso) - new Date(b.iso));
    const limited = allDates.slice(0, 6);
    if (!limited.length) {
      list.innerHTML = '<li class="text-muted">No upcoming matches.</li>';
    } else {
      list.innerHTML = limited.map((item) => {
        return `<li><strong>${item.label}</strong> ${formatter.format(new Date(item.iso))}</li>`;
      }).join('');
    }
  } catch (err) {
    console.error('Preview failed', err);
    list.innerHTML = '<li class="text-danger">Preview failed.</li>';
  } finally {
    spinner.style.display = 'none';
  }
}
