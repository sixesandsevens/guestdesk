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
      const instStart = ev.extendedProps.instance_start;
      // If this is a baseline slot occurrence, take user to Manage Slots
      if (src === 'slot' && serviceId) {
        window.location.href = `/admin/services/${serviceId}/slots`;
        return;
      }
      // Series occurrence: keep quick rename for this instance (placeholder for action modal)
      const title = prompt("Rename just this occurrence (leave blank to keep):", ev.title);
      if (title === null) return;
      fetch('/admin/services/override', {
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ series_id: seriesId, service_id: serviceId, instance_start: instStart, new_title: title })
      }).then(()=> calendar.refetchEvents());
    }
  });
  calendar.render();

  const newBtn = document.getElementById("new-series");
  if (newBtn) newBtn.addEventListener("click", async () => {
    const title = prompt("Service title:");
    if (!title) return;
    const date = prompt("First date (YYYY-MM-DD):");
    if (!date) return;
    const startTime = prompt("Start time (HH:MM, 24h):", "10:00");
    const endTime = prompt("End time (HH:MM, 24h):", "12:00");
    const rrule = prompt("RRULE (or blank for one-off):", "FREQ=MONTHLY;BYDAY=TH;BYSETPOS=3");
    const dtstart = `${date}T${startTime}:00`;
    const dtend = `${date}T${endTime}:00`;
    await fetch('/admin/services/series', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ title, dtstart, dtend, rrule })
    });
    calendar.refetchEvents();
  });
});
