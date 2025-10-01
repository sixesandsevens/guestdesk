document.addEventListener("DOMContentLoaded", function () {
  const el = document.getElementById("calendar");
  if (!el || !window.FullCalendar) return;
  const feed = el.dataset.feed || '/admin/services/feed';
  const calendar = new FullCalendar.Calendar(el, {
    initialView: 'dayGridMonth',
    height: 'auto',
    weekNumbers: false,
    headerToolbar: {
      left: 'prev,next today',
      center: 'title',
      right: 'dayGridMonth,timeGridWeek,timeGridDay'
    },
    events: {
      url: feed,
      failure: () => alert('Failed to load events.')
    },
    editable: true,
    eventDurationEditable: true,
    eventDrop: async function (info) {
      const ev = info.event;
      const seriesId = ev.extendedProps.series_id;
      const serviceId = ev.extendedProps.service_id;
      const instStart = ev.extendedProps.instance_start;
      const payload = {
        series_id: seriesId,
        service_id: serviceId,
        instance_start: instStart,
        new_dtstart: ev.start.toISOString(),
        new_dtend: ev.end ? ev.end.toISOString() : null
      };
      await fetch('/admin/services/override', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      calendar.refetchEvents();
    },
    eventResize: async function (info) {
      const ev = info.event;
      const seriesId = ev.extendedProps.series_id;
      const serviceId = ev.extendedProps.service_id;
      const instStart = ev.extendedProps.instance_start;
      const payload = {
        series_id: seriesId,
        service_id: serviceId,
        instance_start: instStart,
        new_dtstart: ev.start.toISOString(),
        new_dtend: ev.end ? ev.end.toISOString() : null
      };
      await fetch('/admin/services/override', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload)});
      calendar.refetchEvents();
    },
    eventClick: function (info) {
      const ev = info.event;
      const seriesId = ev.extendedProps.series_id;
      const serviceId = ev.extendedProps.service_id;
      const src = ev.extendedProps.source;
      if (src === 'slot' && serviceId) {
        window.location.href = `/admin/services/${serviceId}/slots`;
        return;
      }
      if (seriesId) {
        openEditModal(seriesId);
      }
    }
  });
  calendar.render();

  const newBtn = document.getElementById("new-series");
  const urlParams = new URLSearchParams(window.location.search);
  const sidParam = urlParams.get('service_id');
  let servicesLoaded = false;

  function getCsrfToken() {
    const meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute('content') : '';
  }

  async function loadServices(force = false) {
    const sel = document.getElementById('sched-service');
    if (!sel) return;
    if (servicesLoaded && !force) {
      if (sidParam) {
        sel.value = String(parseInt(sidParam, 10));
        sel.disabled = true;
      } else {
        sel.disabled = false;
      }
      return;
    }
    try {
      const res = await fetch('/admin/services/options');
      const items = await res.json();
      sel.innerHTML = '<option value="">Select a service…</option>' +
        items.map(i => `<option value="${i.id}">${i.name}</option>`).join('');
      if (sidParam) {
        sel.value = String(parseInt(sidParam, 10));
        sel.disabled = true;
      } else {
        sel.disabled = false;
      }
      servicesLoaded = true;
    } catch (e) {
      console.warn('Failed to load services', e);
    }
  }

  async function openEditModal(seriesId) {
    await loadServices();
    const res = await fetch(`/admin/services/series/${seriesId}`);
    if (!res.ok) {
      alert('Could not load schedule details.');
      return false;
    }
    const s = await res.json();
    const modalEl = document.getElementById('rruleModal');
    modalEl.setAttribute('data-edit-id', seriesId);
    document.getElementById('sched-date').value = (s.dtstart || '').slice(0,10);
    document.getElementById('sched-start').value = (s.dtstart || '').slice(11,16) || '10:00';
    document.getElementById('sched-end').value = (s.dtend || '').slice(11,16) || '12:00';
    const sel = document.getElementById('sched-service');
    if (sel) {
      sel.value = s.service_id ? String(s.service_id) : '';
      if (!sidParam) sel.disabled = false;
    }
    document.querySelectorAll('.dow').forEach(cb => { cb.checked = false; });
    document.querySelectorAll('.mn-pos').forEach(cb => { cb.checked = false; });
    document.querySelectorAll('.qp').forEach(b => b.classList.remove('active'));

    const parts = (s.rrule || '').split(';').reduce((acc, item) => {
      const [k, v] = item.split('=');
      if (k && v) acc[k] = v;
      return acc;
    }, {});

    if (parts.FREQ === 'WEEKLY') {
      document.querySelector('[data-bs-target="#weeklyTab"]').click();
      document.getElementById('wk-interval').value = parts.INTERVAL || '1';
      const days = (parts.BYDAY || '').split(',');
      document.querySelectorAll('.dow').forEach(cb => {
        cb.checked = days.includes(cb.value);
      });
    } else if (parts.FREQ === 'MONTHLY') {
      document.querySelector('[data-bs-target="#monthlyTab"]').click();
      if (parts.BYSETPOS && parts.BYDAY) {
        document.getElementById('bysetpos').checked = true;
        const poss = parts.BYSETPOS.split(',');
        document.querySelectorAll('.mn-pos').forEach(cb => {
          cb.checked = poss.includes(cb.value);
        });
        document.getElementById('mn-dow').value = parts.BYDAY;
      } else if (parts.BYMONTHDAY) {
        document.getElementById('bymonthday').checked = true;
        document.getElementById('mn-dom').value = parts.BYMONTHDAY;
      }
    } else {
      document.querySelector('[data-bs-target="#oneoffTab"]').click();
    }

    setSummaryAndRRule();
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
    return true;
  }

  function getSelectedServiceName() {
    const sel = document.getElementById('sched-service');
    if (!sel) return '';
    const opt = sel.options[sel.selectedIndex];
    return opt ? opt.textContent.trim() : '';
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
    document.getElementById('sched-date').value = '';
    document.getElementById('sched-start').value = '10:00';
    document.getElementById('sched-end').value = '12:00';
    document.getElementById('bysetpos').checked = true;
    document.querySelectorAll('.dow').forEach(cb => { cb.checked = false; });
    document.querySelectorAll('.mn-pos').forEach(cb => { cb.checked = false; });
    document.getElementById('mn-dow').value = 'MO';
    document.getElementById('bymonthday').checked = false;
    document.getElementById('mn-dom').value = '1';
    document.querySelectorAll('.qp').forEach(b => b.classList.remove('active'));
    document.querySelector('[data-bs-target="#weeklyTab"]').click();
    setSummaryAndRRule();
  }

  if (newBtn) newBtn.addEventListener('click', async () => {
    await loadServices();
    resetModalFields();
    const sel = document.getElementById('sched-service');
    if (!sidParam && sel) sel.disabled = false;
    const modalEl = document.getElementById('rruleModal');
    if (!modalEl) return;
    const modal = new bootstrap.Modal(modalEl);
    modal.show();
  });

  // === Simple RRULE builder logic ===
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
      const dows = Array.from(document.querySelectorAll('.dow:checked')).map(el => el.value);
      if (dows.length > 0) {
        rrule = `FREQ=WEEKLY;INTERVAL=${interval};BYDAY=${dows.join(',')}`;
        const names = {MO:'Mon',TU:'Tue',WE:'Wed',TH:'Thu',FR:'Fri',SA:'Sat',SU:'Sun'};
        summary = (interval===1? 'Every ' : `Every ${interval} weeks on `) + dows.map(d=>names[d]).join(', ');
      }
    } else if (monthlyActive) {
      const mode = document.querySelector('input[name="monthMode"]:checked').value;
      if (mode === 'bysetpos') {
        const pos = Array.from(document.querySelectorAll('.mn-pos:checked')).map(el => el.value);
        const dow = document.getElementById('mn-dow').value;
        if (pos.length > 0) {
          const posLabelMap = { '1':'1st','2':'2nd','3':'3rd','4':'4th','-1':'Last' };
          const names = {MO:'Monday',TU:'Tuesday',WE:'Wednesday',TH:'Thursday',FR:'Friday',SA:'Saturday',SU:'Sunday'};
          rrule = `FREQ=MONTHLY;BYDAY=${dow};BYSETPOS=${pos.join(',')}`;
          const posText = pos.map(p => posLabelMap[p] || p).join(' & ');
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
        sumEl.textContent = (date && st && et ? `${summary} at ${st}–${et} starting ${date}` : summary);
      } else {
        sumEl.textContent = 'Fill in details…';
      }
    }
  }

  ['sched-date','sched-start','sched-end','wk-interval','mn-dow','mn-dom'].forEach(id=>{
    const el = document.getElementById(id);
    if (el) el.addEventListener('input', setSummaryAndRRule);
  });
  document.querySelectorAll('.dow, input[name="monthMode"]').forEach(el=> el.addEventListener('change', setSummaryAndRRule));
  document.querySelectorAll('.mn-pos').forEach(el=> el.addEventListener('change', setSummaryAndRRule));
  document.querySelectorAll('#freqTabs .nav-link').forEach(el=> el.addEventListener('shown.bs.tab', setSummaryAndRRule));

  // Quick picks
  document.querySelectorAll('.qp').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.qp').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');

      const code = btn.dataset.qp;
      const wkInt = document.getElementById('wk-interval');
      if (wkInt) wkInt.value = 1;
      document.querySelectorAll('.dow').forEach(d=> d.checked = false);
      document.querySelectorAll('.mn-pos').forEach(cb => { cb.checked = false; });
      if (code === 'WEEKDAYS') {
        ['MO','TU','WE','TH','FR'].forEach(v => document.querySelector(`.dow[value="${v}"]`).checked = true);
        document.querySelector('[data-bs-target="#weeklyTab"]').click();
      } else if (code === 'WEEKENDS') {
        ['SA','SU'].forEach(v => document.querySelector(`.dow[value="${v}"]`).checked = true);
        document.querySelector('[data-bs-target="#weeklyTab"]').click();
      } else if (code === 'EVERY_OTHER_TU') {
        document.getElementById('wk-interval').value = 2;
        document.querySelector(`.dow[value="TU"]`).checked = true;
        document.querySelector('[data-bs-target="#weeklyTab"]').click();
      } else if (code === 'SECOND_FOURTH_MO') {
        document.querySelector('[data-bs-target="#monthlyTab"]').click();
        document.getElementById('bysetpos').checked = true;
        document.querySelectorAll('.mn-pos').forEach(cb => { cb.checked = false; });
        document.querySelector('.mn-pos[value="2"]').checked = true;
        document.querySelector('.mn-pos[value="4"]').checked = true;
        document.getElementById('mn-dow').value = 'MO';
      } else if (code === 'LAST_FR') {
        document.querySelector('[data-bs-target="#monthlyTab"]').click();
        document.getElementById('bysetpos').checked = true;
        document.querySelectorAll('.mn-pos').forEach(cb => { cb.checked = false; });
        document.querySelector('.mn-pos[value="-1"]').checked = true;
        document.getElementById('mn-dow').value = 'FR';
      }
      setSummaryAndRRule();
    });
  });

  // Save handler
  const saveBtn = document.getElementById('sched-save');
  if (saveBtn) saveBtn.addEventListener('click', async () => {
    const date = document.getElementById('sched-date').value;
    const st = document.getElementById('sched-start').value;
    const et = document.getElementById('sched-end').value;
    const rrule = document.getElementById('sched-rrule').value;
    const sel = document.getElementById('sched-service');
    const serviceId = sidParam || (sel ? sel.value : '');

    if (!serviceId) { alert('Please choose a service.'); return; }
    if (!date || !st || !et) { alert('Please choose date and time.'); return; }

    const serviceName = getSelectedServiceName();

    const payload = {
      title: serviceName || 'Untitled Service',
      dtstart: `${date}T${st}:00`,
      dtend: `${date}T${et}:00`,
      rrule,
      service_id: parseInt(serviceId, 10)
    };

    const modalEl = document.getElementById('rruleModal');
    const editId = modalEl.getAttribute('data-edit-id');
    const method = editId ? 'PUT' : 'POST';
    const url = editId ? `/admin/services/series/${editId}` : '/admin/services/series';

    try {
      const resp = await fetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken(),
        },
        body: JSON.stringify(payload)
      });
      if (!resp.ok) {
        const msg = await resp.text();
        throw new Error(`Save failed (${resp.status}): ${msg}`);
      }
    } catch (e) {
      console.error(e);
      alert('Failed to save schedule.');
      return;
    }

    modalEl.removeAttribute('data-edit-id');
    const modal = bootstrap.Modal.getInstance(modalEl);
    modal && modal.hide();
    await renderSeriesTable();
    setTimeout(()=> calendar.refetchEvents(), 200);
  });

  async function fetchSeriesForService(serviceId) {
    const res = await fetch(`/admin/services/series?service_id=${serviceId}`);
    if (!res.ok) {
      throw new Error(`Failed to load series (${res.status})`);
    }
    return await res.json();
  }

  async function renderSeriesTable() {
    const table = document.querySelector('#series-table tbody');
    if (!table) return;
    const selectEl = document.getElementById('sched-service');
    const sid = sidParam || (selectEl ? selectEl.value : '');
    if (!sid) {
      table.innerHTML = '<tr><td colspan="4" class="text-muted p-3">Select a service to see its schedules.</td></tr>';
      return;
    }
    try {
      const rows = await fetchSeriesForService(sid);
      if (!rows.length) {
        table.innerHTML = '<tr><td colspan="4" class="text-muted p-3">No schedules yet.</td></tr>';
        return;
      }
      table.innerHTML = rows.map(r => {
        const displayName = (r.service_name || '').trim() || (r.title || '');
        return `
        <tr data-id="${r.id}" data-service="${r.service_id || ''}">
          <td>${displayName}</td>
          <td><code>${r.rrule || ''}</code></td>
          <td>${(r.dtstart || '').slice(0,16)} – ${(r.dtend || '').slice(11,16)}</td>
          <td>
            <button class="btn btn-sm btn-outline-primary me-1 act-edit">Edit</button>
            <button class="btn btn-sm btn-outline-danger act-del">Delete</button>
          </td>
        </tr>`;
      }).join('');
    } catch (e) {
      table.innerHTML = '<tr><td colspan="4" class="text-danger p-3">Failed to load schedules.</td></tr>';
    }
  }

  document.getElementById('sched-service')?.addEventListener('change', () => {
    if (!sidParam) renderSeriesTable();
  });

  document.addEventListener('click', async (e) => {
    const btn = e.target;
    if (!(btn instanceof HTMLElement)) return;
    const row = btn.closest('tr[data-id]');
    if (!row) return;
    const id = row.getAttribute('data-id');
    if (btn.classList.contains('act-del')) {
      if (!confirm('Delete this schedule?')) return;
      const resp = await fetch(`/admin/services/series/${id}`, {
        method: 'DELETE',
        headers: {
          'X-CSRFToken': getCsrfToken(),
        },
      });
      if (!resp.ok) {
        const msg = await resp.text();
        console.error('Delete failed', msg);
        alert('Failed to delete schedule.');
        return;
      }
      await renderSeriesTable();
      calendar.refetchEvents();
      return;
    }
    if (btn.classList.contains('act-edit')) {
      await openEditModal(id);
    }
  });

  document.getElementById('rruleModal')?.addEventListener('hidden.bs.modal', () => {
    const modalEl = document.getElementById('rruleModal');
    modalEl.removeAttribute('data-edit-id');
    if (!sidParam) {
      const sel = document.getElementById('sched-service');
      if (sel) sel.disabled = false;
    }
  });

  // Initialize summary on load
  setSummaryAndRRule();
  loadServices();
  renderSeriesTable();
});
