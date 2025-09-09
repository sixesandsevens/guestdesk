// Admin analytics dashboard
(async function() {
  const $ = (sel) => document.querySelector(sel);
  const qs = () => {
    const f = $("#from").value, t = $("#to").value;
    return (f && t) ? `?from=${f}&to=${t}` : "";
  };
  function setPreset(days) {
    const to = new Date(); const from = new Date(); from.setDate(to.getDate() - (days - 1));
    $("#from").value = from.toISOString().slice(0,10);
    $("#to").value = to.toISOString().slice(0,10);
  }

  // Init defaults
  setPreset(30);
  document.querySelectorAll("[data-preset]").forEach(b => b.addEventListener("click", e => setPreset(parseInt(b.dataset.preset))));
  $("#apply").addEventListener("click", refresh);

  let tsChart, catChart;

  async function fetchJSON(url) { const r = await fetch(url + qs()); return r.json(); }

  async function refresh(){
    const [sum, ts, pages, flows, cats, forms, perf] = await Promise.all([
      fetchJSON("/admin/analytics/api/summary"),
      fetchJSON("/admin/analytics/api/timeseries"),
      fetchJSON("/admin/analytics/api/top-pages"),
      fetchJSON("/admin/analytics/api/flows"),
      fetchJSON("/admin/analytics/api/categories"),
      fetchJSON("/admin/analytics/api/forms"),
      fetchJSON("/admin/analytics/api/perf")
    ]);

    // Summary
    $("#sum-total").textContent = sum.total;
    $("#sum-uniques").textContent = sum.uniques;
    $("#sum-forms").textContent = sum.form_submissions;
    $("#sum-staff").textContent = sum.staff;
    $("#sum-guests").textContent = sum.guests;

    // Timeseries
    const labels = ts.map(r => r.date);
    const hits = ts.map(r => r.hits);
    const uniq = ts.map(r => r.uniques);
    if (tsChart) tsChart.destroy();
    tsChart = new Chart($("#ts"), {
      type: "line",
      data: { labels, datasets: [
        { label:"Hits", data:hits },
        { label:"Uniques", data:uniq }
      ]},
      options: { responsive:true, maintainAspectRatio:false, animation:false, resizeDelay: 150 }
    });

    // Top pages
    const tbodyPages = $("#tbl-pages tbody"); tbodyPages.innerHTML = "";
    pages.forEach(r => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${r.path}</td><td>${r.views}</td><td>${r.avg_ms}</td>`;
      tbodyPages.appendChild(tr);
    });

    // Flows
    const tbodyFlows = $("#tbl-flows tbody"); tbodyFlows.innerHTML = "";
    flows.forEach(r => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${r.from || "(direct)"}</td><td>→</td><td>${r.to}</td><td>${r.count}</td>`;
      tbodyFlows.appendChild(tr);
    });

    // Categories
    const catLabels = cats.map(x => x.category);
    const catCounts = cats.map(x => x.count);
    if (catChart) catChart.destroy();
    catChart = new Chart($("#cats"), {
      type: "doughnut",
      data: { labels: catLabels, datasets: [{ data: catCounts }] },
      options: { responsive:true, animation:false, resizeDelay: 150 }
    });

    // Forms
    const tbodyForms = $("#tbl-forms tbody"); tbodyForms.innerHTML = "";
    forms.forEach(r => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${r.form}</td><td>${r.count}</td>`;
      tbodyForms.appendChild(tr);
    });

    // Perf
    const tbodyPerf = $("#tbl-perf tbody"); tbodyPerf.innerHTML = "";
    perf.forEach(r => {
      const tr = document.createElement("tr");
      tr.innerHTML = `<td>${r.path}</td><td>${r.avg_ms}</td>`;
      tbodyPerf.appendChild(tr);
    });
  }

  refresh();
})();
