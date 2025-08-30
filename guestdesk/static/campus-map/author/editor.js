(function(){
  const svg = document.getElementById('svg');
  const viewport = document.getElementById('viewport');
  const featuresG = document.getElementById('features');
  const tempG = document.getElementById('temp');
  const bg = document.getElementById('bg');

  const bgFile = document.getElementById('bgFile');
  const fitBtn = document.getElementById('fitBtn');
  const polyBtn = document.getElementById('polyBtn');
  const finishBtn = document.getElementById('finishBtn');
  const cancelBtn = document.getElementById('cancelBtn');
  const nameInput = document.getElementById('nameInput');
  const typeInput = document.getElementById('typeInput');
  const servicesInput = document.getElementById('servicesInput');
  const targetInput = document.getElementById('targetInput');
  const dupBtn = document.getElementById('dupBtn');
  const delBtn = document.getElementById('delBtn');
  const expSvg = document.getElementById('expSvg');
  const expGeo = document.getElementById('expGeo');

  // Pan/zoom state
  let scale = 1, tx = 0, ty = 0;
  function applyView(){ viewport.setAttribute('transform', `translate(${tx},${ty}) scale(${scale})`); }
  applyView();

  let isPanning = false, lastX=0,lastY=0;
  svg.addEventListener('mousedown', (e)=>{
    if (e.target === svg) { isPanning = true; lastX=e.clientX; lastY=e.clientY; }
  });
  window.addEventListener('mousemove', (e)=>{
    if(isPanning){ tx += (e.clientX-lastX); ty += (e.clientY-lastY); lastX=e.clientX; lastY=e.clientY; applyView(); }
  });
  window.addEventListener('mouseup', ()=> isPanning=false);
  svg.addEventListener('wheel', (e)=>{
    e.preventDefault();
    const k = Math.exp(-e.deltaY*0.0015);
    const pt = clientToLocal(e.clientX, e.clientY);
    const preX = (pt.x - tx)/scale, preY = (pt.y - ty)/scale;
    scale *= k;
    const postX = preX, postY = preY;
    tx = pt.x - postX*scale; ty = pt.y - postY*scale;
    applyView();
  }, {passive:false});

  window.addEventListener('keydown', (e)=>{
    if(e.key === '0'){ scale=1; tx=ty=0; applyView(); }
  });

  function clientToLocal(cx, cy){
    const rect = svg.getBoundingClientRect();
    const x = cx - rect.left, y = cy - rect.top;
    return { x, y };
  }

  // Load background image
  bgFile.addEventListener('change', async (e)=>{
    const file = e.target.files[0];
    const url = URL.createObjectURL(file);
    const img = new Image();
    await new Promise(res=>{ img.onload=res; img.src=url; });
    bg.setAttribute('href', url);
    bg.setAttribute('width', img.width);
    bg.setAttribute('height', img.height);
    bg.setAttribute('visibility','visible');
    fitToScreen(img.width, img.height);
  });
  function fitToScreen(w, h){
    const rect = svg.getBoundingClientRect();
    const s = Math.min(rect.width/w, rect.height/h) * 0.95;
    scale = s; tx = (rect.width - w*s)/2; ty = (rect.height - h*s)/2; applyView();
  }
  fitBtn.addEventListener('click', ()=>{
    const w = parseFloat(bg.getAttribute('width')||'0');
    const h = parseFloat(bg.getAttribute('height')||'0');
    if(w && h) fitToScreen(w,h);
  });

  // Authoring state
  let mode = 'idle';
  let currentPoly = null;
  let selected = null;
  let isRotating = false;
  let rotateStart = null;

  polyBtn.addEventListener('click', ()=>{
    mode='draw'; currentPoly = makePolygon(); tempG.appendChild(currentPoly.g); refreshButtons();
  });
  finishBtn.addEventListener('click', ()=> finishDraw());
  cancelBtn.addEventListener('click', ()=> cancelDraw());

  function refreshButtons(){
    const drawing = (mode==='draw');
    finishBtn.disabled = !drawing;
    cancelBtn.disabled = !drawing;
    dupBtn.disabled = !selected;
    delBtn.disabled = !selected;
  }

  function makePolygon(points=[]){
    const g = document.createElementNS('http://www.w3.org/2000/svg','g');
    const p = document.createElementNS('http://www.w3.org/2000/svg','polygon');
    p.setAttribute('class','feature ghost');
    p.setAttribute('points', points.map(pt=>`${pt[0]},${pt[1]}`).join(' '));
    g.appendChild(p);
    g.dataset.name='';
    g.dataset.type='';
    g.dataset.services='';
    g.dataset.target='';
    const verticesG = document.createElementNS('http://www.w3.org/2000/svg','g');
    g.appendChild(verticesG);
    return {g, p, verticesG, points};
  }

  svg.addEventListener('dblclick', (e)=>{
    if(mode==='draw'){ finishDraw(); }
  });

  svg.addEventListener('mousedown', (e)=>{
    if(mode==='draw'){
      const pt = clientToDoc(e);
      currentPoly.points.push([pt.x, pt.y]);
      updatePoly(currentPoly);
    }
  });

  function finishDraw(){
    if(currentPoly && currentPoly.points.length>=3){
      currentPoly.p.classList.remove('ghost');
      featuresG.appendChild(currentPoly.g);
      wireFeature(currentPoly);
      select(currentPoly);
    }else{
      // discard
      if(currentPoly) tempG.removeChild(currentPoly.g);
    }
    currentPoly = null; mode='idle'; refreshButtons();
  }

  function cancelDraw(){
    if(currentPoly) tempG.removeChild(currentPoly.g);
    currentPoly=null; mode='idle'; refreshButtons();
  }

  function clientToDoc(e){
    const {x,y} = clientToLocal(e.clientX, e.clientY);
    return { x:(x - tx)/scale, y:(y - ty)/scale };
  }

  function updatePoly(poly){
    poly.p.setAttribute('points', poly.points.map(pt=>pt.join(',')).join(' '));
    // rebuild vertices
    while(poly.verticesG.firstChild) poly.verticesG.removeChild(poly.verticesG.firstChild);
    poly.points.forEach((pt, i)=>{
      const c = document.createElementNS('http://www.w3.org/2000/svg','circle');
      c.setAttribute('class','vertex');
      c.setAttribute('cx', pt[0]); c.setAttribute('cy', pt[1]); c.setAttribute('r', 4.5);
      poly.verticesG.appendChild(c);
      let dragging = false;
      c.addEventListener('mousedown', (e)=>{ e.stopPropagation(); dragging=true; });
      window.addEventListener('mousemove', (e)=>{
        if(!dragging) return;
        const d = clientToDoc(e);
        poly.points[i] = [d.x, d.y];
        updatePoly(poly);
      });
      window.addEventListener('mouseup', ()=> dragging=false);
    });
  }

  function centroid(points){
    let x=0,y=0; for(const [px,py] of points){ x+=px; y+=py; } return {x:x/points.length, y:y/points.length};
  }

  function rotatePoints(points, cx, cy, angle){
    const s=Math.sin(angle), c=Math.cos(angle);
    return points.map(([x,y])=>{
      const dx = x - cx, dy = y - cy;
      return [cx + dx*c - dy*s, cy + dx*s + dy*c];
    });
  }

  function wireFeature(poly){
    poly.p.classList.add('feature');
    poly.p.addEventListener('mousedown', (e)=>{
      e.stopPropagation();
      select(poly);
      // drag whole feature
      let dragging = true;
      const start = clientToDoc(e);
      const orig = poly.points.map(p=>[...p]);
      window.addEventListener('mousemove', moveHandler);
      window.addEventListener('mouseup', upHandler);
      function moveHandler(ev){
        if(!dragging || isRotating) return;
        const now = clientToDoc(ev);
        const dx = now.x - start.x, dy = now.y - start.y;
        poly.points = orig.map(([x,y])=>[x+dx,y+dy]);
        updatePoly(poly);
      }
      function upHandler(){ dragging=false; window.removeEventListener('mousemove',moveHandler); window.removeEventListener('mouseup',upHandler); }
    });
    updatePoly(poly);
  }

  function select(poly){
    if(selected && selected!==poly) selected.p.classList.remove('selected');
    selected = poly;
    if(selected) selected.p.classList.add('selected');
    // load fields
    nameInput.value = selected?.g.dataset.name || '';
    typeInput.value = selected?.g.dataset.type || '';
    servicesInput.value = selected?.g.dataset.services || '';
    targetInput.value = selected?.g.dataset.target || '';
    refreshButtons();
  }

  [nameInput, typeInput, servicesInput, targetInput].forEach(input=>{
    input.addEventListener('input', ()=>{
      if(!selected) return;
      selected.g.dataset.name = nameInput.value.trim();
      selected.g.dataset.type = typeInput.value.trim();
      selected.g.dataset.services = servicesInput.value.trim();
      selected.g.dataset.target = targetInput.value.trim();
      // show name as title for handy hover
      let t = selected.g.querySelector('title');
      if(!t){ t = document.createElementNS('http://www.w3.org/2000/svg','title'); selected.g.appendChild(t); }
      t.textContent = selected.g.dataset.name || '(unnamed)';
    });
  });

  // Rotate: hold R; Shift+R => 15° snapping
  window.addEventListener('keydown', (e)=>{
    if(e.key.toLowerCase()==='r' && selected){ isRotating = true; rotateStart = null; }
    if(e.key.toLowerCase()==='d' && selected){ duplicate(); }
    if(e.key === 'Delete' && selected){ deleteSel(); }
  });
  window.addEventListener('keyup', (e)=>{
    if(e.key.toLowerCase()==='r'){ isRotating = false; rotateStart=null; }
  });
  window.addEventListener('mousemove', (e)=>{
    if(!isRotating || !selected) return;
    const doc = clientToDoc(e);
    const c = centroid(selected.points);
    if(!rotateStart){ rotateStart = doc; return; }
    const a0 = Math.atan2(rotateStart.y - c.y, rotateStart.x - c.x);
    const a1 = Math.atan2(doc.y - c.y, doc.x - c.x);
    let da = a1 - a0;
    if (window.event.shiftKey) {
      const step = Math.PI/12; // 15°
      da = Math.round(da/step)*step;
    }
    selected.points = rotatePoints(selected.points, c.x, c.y, da);
    updatePoly(selected);
    rotateStart = doc;
  });

  function duplicate(){
    if(!selected) return;
    const pts = selected.points.map(([x,y])=>[x+10,y+10]);
    const p2 = makePolygon(pts);
    p2.g.dataset.name = (selected.g.dataset.name||'') + ' (copy)';
    p2.g.dataset.type = selected.g.dataset.type || '';
    p2.g.dataset.services = selected.g.dataset.services || '';
    p2.g.dataset.target = selected.g.dataset.target || '';
    featuresG.appendChild(p2.g);
    wireFeature(p2);
    select(p2);
  }
  dupBtn.addEventListener('click', duplicate);

  function deleteSel(){
    if(!selected) return;
    featuresG.removeChild(selected.g); selected = null;
    refreshButtons();
  }
  delBtn.addEventListener('click', deleteSel);

  // Export SVG
  expSvg.addEventListener('click', ()=>{
    const w = parseFloat(bg.getAttribute('width')||'1500');
    const h = parseFloat(bg.getAttribute('height')||'1000');
    const ser = [];
    ser.push(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${w} ${h}">`);
    // embed raster as an <image> layer for context (can be removed later)
    const href = bg.getAttribute('href')||'';
    if(href) ser.push(`<image href="${href}" x="0" y="0" width="${w}" height="${h}" opacity="0.15"></image>`);
    ser.push(`<g id="features">`);
    featuresG.querySelectorAll(':scope > g').forEach(g=>{
      const poly = g.querySelector('polygon');
      const pts = poly.getAttribute('points');
      const attrs = [
        ['data-role','feature'],
        ['data-name', g.dataset.name||''],
        ['data-type', g.dataset.type||''],
        ['data-services', g.dataset.services||''],
        ['data-target', g.dataset.target||'']
      ].map(([k,v])=> v ? `${k}="${escapeAttr(v)}"` : '').filter(Boolean).join(' ');
      ser.push(`<polygon class="feature" points="${pts}" ${attrs}></polygon>`);
    });
    ser.push(`</g></svg>`);
    download('overlay.svg', ser.join(''));
  });

  function escapeAttr(v){ return String(v).replace(/&/g,'&amp;').replace(/"/g,'&quot;'); }

  // Export GeoJSON
  expGeo.addEventListener('click', ()=>{
    const feats = [];
    featuresG.querySelectorAll(':scope > g').forEach(g=>{
      const poly = g.querySelector('polygon');
      const pts = poly.getAttribute('points').trim().split(/\s+/).map(s=>s.split(',').map(Number));
      // close ring
      const ring = pts[0][0]===pts[pts.length-1][0] && pts[0][1]===pts[pts.length-1][1] ? pts : pts.concat([pts[0]]);
      feats.push({
        type:'Feature',
        properties:{
          name: g.dataset.name||'',
          type: g.dataset.type||'',
          services: (g.dataset.services||'').split(',').map(s=>s.trim()).filter(Boolean),
          target: g.dataset.target||''
        },
        geometry:{
          type:'Polygon',
          coordinates: [ ring ]
        }
      });
    });
    const fc = { type:'FeatureCollection', features:feats };
    download('features.json', JSON.stringify(fc, null, 2));
  });

  function download(name, content){
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([content], {type:'application/octet-stream'}));
    a.download = name; a.click();
  }

})();