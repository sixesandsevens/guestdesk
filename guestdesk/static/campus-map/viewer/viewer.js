(function(){
  const svg = document.getElementById('svg');
  const viewport = document.getElementById('viewport');
  const featuresG = document.getElementById('features');
  const labelsG = document.getElementById('labels');
  const bg = document.getElementById('bg');
  const bgFile = document.getElementById('bgFile');
  const overlayFile = document.getElementById('overlayFile');
  const fitBtn = document.getElementById('fitBtn');
  const searchInput = document.getElementById('searchInput');
  const filtersDiv = document.getElementById('filters');
  const details = document.getElementById('details');

  // Pan/zoom
  let scale = 1, tx = 0, ty = 0;
  function applyView(){ viewport.setAttribute('transform', `translate(${tx},${ty}) scale(${scale})`); }
  applyView();
  let isPanning=false,lastX=0,lastY=0;
  svg.addEventListener('mousedown',(e)=>{ if(e.target===svg){ isPanning=true; lastX=e.clientX; lastY=e.clientY; }});
  window.addEventListener('mousemove',(e)=>{ if(isPanning){ tx += (e.clientX-lastX); ty += (e.clientY-lastY); lastX=e.clientX; lastY=e.clientY; applyView(); }});
  window.addEventListener('mouseup',()=> isPanning=false);
  svg.addEventListener('wheel',(e)=>{
    e.preventDefault();
    const k = Math.exp(-e.deltaY*0.0015);
    const pt = clientToLocal(e.clientX,e.clientY);
    const preX = (pt.x - tx)/scale, preY = (pt.y - ty)/scale;
    scale *= k;
    tx = pt.x - preX*scale; ty = pt.y - preY*scale;
    applyView();
  }, {passive:false});
  window.addEventListener('keydown',(e)=>{ if(e.key==='0'){ scale=1; tx=ty=0; applyView(); } });

  function clientToLocal(cx,cy){ const r=svg.getBoundingClientRect(); return {x:cx-r.left, y:cy-r.top}; }
  function fitToScreen(w,h){
    const rect = svg.getBoundingClientRect();
    const s = Math.min(rect.width/w, rect.height/h)*0.95;
    scale=s; tx=(rect.width - w*s)/2; ty=(rect.height - h*s)/2; applyView();
  }

  bgFile.addEventListener('change', async (e)=>{
    const file=e.target.files[0];
    const url=URL.createObjectURL(file);
    const img=new Image(); await new Promise(res=>{img.onload=res; img.src=url;});
    bg.setAttribute('href', url); bg.setAttribute('width', img.width); bg.setAttribute('height', img.height);
    bg.setAttribute('visibility','visible');
    fitToScreen(img.width, img.height);
  });
  fitBtn.addEventListener('click', ()=>{
    const w=parseFloat(bg.getAttribute('width')||'0'); const h=parseFloat(bg.getAttribute('height')||'0');
    if(w&&h) fitToScreen(w,h);
  });

  overlayFile.addEventListener('change', (e)=>{
    const file=e.target.files[0];
    const name=file.name.toLowerCase();
    const reader=new FileReader();
    reader.onload=()=>{
      const text=reader.result;
      if(name.endsWith('.svg')) loadSvgOverlay(text);
      else loadGeoJSON(JSON.parse(text));
    };
    reader.readAsText(file);
  });

  function clearOverlay(){ featuresG.innerHTML=''; labelsG.innerHTML=''; filtersDiv.innerHTML=''; details.textContent='Select a feature…'; }

  function loadSvgOverlay(text){
    clearOverlay();
    const tmp = new DOMParser().parseFromString(text, 'image/svg+xml');
    tmp.querySelectorAll('[data-role="feature"]').forEach(el=>{
      const poly = document.createElementNS('http://www.w3.org/2000/svg','polygon');
      poly.setAttribute('class','feature');
      poly.setAttribute('points', el.getAttribute('points'));
      poly.dataset.name = el.getAttribute('data-name')||'';
      poly.dataset.type = el.getAttribute('data-type')||'';
      poly.dataset.services = el.getAttribute('data-services')||'';
      poly.dataset.target = el.getAttribute('data-target')||'';
      addFeature(poly);
    });
    buildFilters();
  }

  function loadGeoJSON(fc){
    clearOverlay();
    fc.features.forEach(f=>{
      if(f.geometry.type!=='Polygon') return;
      const pts = f.geometry.coordinates[0].map(([x,y])=>x+','+y).join(' ');
      const poly = document.createElementNS('http://www.w3.org/2000/svg','polygon');
      poly.setAttribute('class','feature');
      poly.setAttribute('points', pts);
      poly.dataset.name = f.properties.name||'';
      poly.dataset.type = f.properties.type||'';
      poly.dataset.services = (f.properties.services||[]).join(',');
      poly.dataset.target = f.properties.target||'';
      addFeature(poly);
    });
    buildFilters();
  }

  function addFeature(poly){
    featuresG.appendChild(poly);
    poly.addEventListener('click',(e)=>{
      featuresG.querySelectorAll('.selected').forEach(n=>n.classList.remove('selected'));
      poly.classList.add('selected');
      const n = poly.dataset.name||'(unnamed)';
      const t = poly.dataset.type||'';
      const s = (poly.dataset.services||'').split(',').filter(Boolean);
      const k = poly.dataset.target||'';
      details.innerHTML = `<div><b>${escape(n)}</b></div>
        <div class="badge">${escape(t)}</div>
        ${s.map(x=>`<span class="badge">${escape(x)}</span>`).join('')}
        ${k?`<div style="margin-top:8px;"><a href="${k}" target="_blank" rel="noopener">Open target</a></div>`:''}`;
      zoomTo(poly);
    });
  }

  function escape(s){ return String(s).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }

  function bboxOf(poly){
    const pts = poly.getAttribute('points').trim().split(/\s+/).map(s=>s.split(',').map(Number));
    let minX=Infinity,minY=Infinity,maxX=-Infinity,maxY=-Infinity;
    pts.forEach(([x,y])=>{ if(x<minX)minX=x; if(y<minY)minY=y; if(x>maxX)maxX=x; if(y>maxY)maxY=y; });
    return {x:minX,y:minY,w:maxX-minX,h:maxY-minY};
  }

  function zoomTo(poly){
    const b = bboxOf(poly);
    const rect = svg.getBoundingClientRect();
    const pad = 30;
    const s = Math.min((rect.width-2*pad)/b.w, (rect.height-2*pad)/b.h);
    scale = Math.min(s, 6);
    tx = -b.x*scale + pad; ty = -b.y*scale + pad;
    applyView();
  }

  function buildFilters(){
    const types = new Set();
    const services = new Set();
    featuresG.querySelectorAll('polygon').forEach(p=>{
      const t = (p.dataset.type||'').trim(); if(t) types.add(t);
      (p.dataset.services||'').split(',').map(s=>s.trim()).filter(Boolean).forEach(s=>services.add(s));
    });

    function makeChip(label, group){
      const span=document.createElement('span');
      span.className='filter'; span.textContent=label;
      span.addEventListener('click', ()=>{ span.classList.toggle('active'); applyFilter(); });
      span.dataset.group=group;
      return span;
    }

    filtersDiv.innerHTML='';
    types.forEach(t=> filtersDiv.appendChild(makeChip(t,'type')));
    services.forEach(s=> filtersDiv.appendChild(makeChip(s,'service')));

    searchInput.addEventListener('input', applyFilter);
    function applyFilter(){
      const activeTypes = [...filtersDiv.querySelectorAll('.filter.active[data-group="type"]')].map(n=>n.textContent);
      const activeServs = [...filtersDiv.querySelectorAll('.filter.active[data-group="service"]')].map(n=>n.textContent);
      const q = searchInput.value.toLowerCase().trim();
      featuresG.querySelectorAll('polygon').forEach(p=>{
        const name = (p.dataset.name||'').toLowerCase();
        const t = (p.dataset.type||'');
        const servs = (p.dataset.services||'').split(',').filter(Boolean);
        let ok = true;
        if(activeTypes.length && !activeTypes.includes(t)) ok=false;
        if(activeServs.length && !activeServs.every(s=>servs.includes(s))) ok=false;
        if(q && !name.includes(q)) ok=false;
        p.style.display = ok ? '' : 'none';
      });
    }
  }

})();