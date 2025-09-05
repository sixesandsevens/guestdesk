// Funzone mobile enhancements: bigger controls, D-pad, hold-to-repeat, swipe
(function(){
  function onReady(fn){ if(document.readyState!=='loading') fn(); else document.addEventListener('DOMContentLoaded', fn); }
  onReady(function(){
    try{
      // Inject mobile control styles
      var css = [
        '/* Injected mobile touchbar upgrades */',
        '.touchbar{display:none}',
        '@media (max-width:768px){',
        ' .game-wrap{position:relative;width:100%;padding-bottom:96px}',
        ' .touchbar{display:flex;position:absolute;left:50%;transform:translateX(-50%);',
        '  bottom:calc(10px + env(safe-area-inset-bottom));gap:12px;',
        '  background:rgba(15,18,32,.45);padding:10px;border-radius:16px;z-index:10;',
        '  box-shadow:0 4px 20px rgba(0,0,0,.35);-webkit-backdrop-filter:blur(6px);backdrop-filter:blur(6px);',
        '  align-items:center;justify-content:space-between}',
        ' .touchbar .btn{width:64px;height:64px;padding:0;display:flex;align-items:center;justify-content:center;',
        '  touch-action:none;user-select:none;font-size:20px;border-radius:14px}',
        ' .touchbar--split{width:min(92vw,840px)}',
        ' .cluster{display:flex;gap:10px;align-items:center}',
        ' .dpad{display:grid;grid-template-columns:repeat(3,64px);grid-template-rows:repeat(3,64px);gap:8px}',
        ' .dpad .spacer{visibility:hidden}',
        ' .dpad .up{grid-column:2;grid-row:1}',
        ' .dpad .left{grid-column:1;grid-row:2}',
        ' .dpad .right{grid-column:3;grid-row:2}',
        ' .dpad .down{grid-column:2;grid-row:3}',
        ' .game-wrap canvas{touch-action:none}',
        '}'
      ].join('\n');
      var st=document.createElement('style'); st.textContent=css; document.head.appendChild(st);
    }catch(_){ }

    function qs(s){ return document.querySelector(s); }

    // Upgrade Printer Jam touchbar to split layout
    try{
      var jamTB = qs('#panel-printerjam .touchbar');
      if (jamTB){
        jamTB.classList.remove('touchbar--below');
        jamTB.classList.add('touchbar--split');
        jamTB.setAttribute('aria-hidden','true');
        jamTB.innerHTML = ''+
          '<div class="cluster" aria-label="Move paddle">'+
          '  <button class="btn" id="jamLeft" aria-label="Move left">←</button>'+
          '  <button class="btn" id="jamRight" aria-label="Move right">→</button>'+
          '</div>'+
          '<div class="cluster" aria-label="Game controls">'+
          '  <button class="btn" id="jamPauseTouch" aria-label="Pause">⏸</button>'+
          '  <button class="btn" id="jamStartTouch" aria-label="Start">▶</button>'+
          '</div>';
        // Bind hold using keyboard events so game picks it up
        (function(){
          function dispatch(type,key){ try{ window.dispatchEvent(new KeyboardEvent(type,{key:key,bubbles:true})); }catch(_){ } }
          function bindHoldKey(id,key){ var el=document.getElementById(id); if(!el) return; var down=function(e){ e.preventDefault(); dispatch('keydown',key); }; var up=function(){ dispatch('keyup',key); }; ['touchstart','mousedown'].forEach(function(ev){ el.addEventListener(ev,down,{passive:false}); }); ['touchend','touchcancel','mouseup','mouseleave'].forEach(function(ev){ el.addEventListener(ev,up); }); }
          bindHoldKey('jamLeft','ArrowLeft');
          bindHoldKey('jamRight','ArrowRight');
          var pauseBtn=document.getElementById('jamPauseTouch'); if(pauseBtn){ ['touchstart','click'].forEach(function(ev){ pauseBtn.addEventListener(ev,function(e){ e.preventDefault(); try{ window.dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true})); }catch(_){ } }); }); }
          var startBtn=document.getElementById('jamStartTouch'); if(startBtn){ ['touchstart','click'].forEach(function(ev){ startBtn.addEventListener(ev,function(e){ e.preventDefault(); try{ window.dispatchEvent(new KeyboardEvent('keydown',{key:' ',bubbles:true})); }catch(_){ } }); }); }
        })();
      }
    }catch(_){ }

    // Upgrade Duck touchbar to D-pad + controls
    try{
      var duckTB = qs('#panel-duck .touchbar');
      if (duckTB){
        duckTB.classList.remove('touchbar--below');
        duckTB.classList.add('touchbar--split');
        duckTB.setAttribute('aria-hidden','true');
        duckTB.innerHTML = ''+
          '<div class="cluster dpad" aria-label="Duck D-pad">'+
          '  <button class="btn up" id="duckUp" aria-label="Up">↑</button>'+
          '  <button class="btn left" id="duckLeft" aria-label="Left">←</button>'+
          '  <div class="spacer"></div>'+
          '  <button class="btn right" id="duckRight" aria-label="Right">→</button>'+
          '  <button class="btn down" id="duckDown" aria-label="Down">↓</button>'+
          '</div>'+
          '<div class="cluster" aria-label="Game controls">'+
          '  <button class="btn" id="duckPauseTouch" aria-label="Pause">⏸</button>'+
          '  <button class="btn" id="duckStartTouch" aria-label="Start">▶</button>'+
          '</div>';
      }
    }catch(_){ }

    // Add Snake touchbar if missing (wrap canvas)
    try{
      var snakeCanvas = document.getElementById('snakeCanvas');
      if (snakeCanvas && !snakeCanvas.parentElement.classList.contains('game-wrap')){
        var wrap = document.createElement('div'); wrap.className = 'game-wrap';
        snakeCanvas.parentElement.insertBefore(wrap, snakeCanvas);
        wrap.appendChild(snakeCanvas);
        var tb = document.createElement('div');
        tb.className = 'touchbar touchbar--split'; tb.setAttribute('aria-hidden','true');
        tb.innerHTML = ''+
          '<div class="cluster dpad" aria-label="Snake D-pad">'+
          '  <button class="btn up" id="snakeUp" aria-label="Up">↑</button>'+
          '  <button class="btn left" id="snakeLeft" aria-label="Left">←</button>'+
          '  <div class="spacer"></div>'+
          '  <button class="btn right" id="snakeRight" aria-label="Right">→</button>'+
          '  <button class="btn down" id="snakeDown" aria-label="Down">↓</button>'+
          '</div>'+
          '<div class="cluster" aria-label="Snake controls">'+
          '  <button class="btn" id="snakePauseTouch" aria-label="Pause">⏸</button>'+
          '  <button class="btn" id="snakeStartTouch" aria-label="Start">▶</button>'+
          '</div>';
        wrap.appendChild(tb);
      }
    }catch(_){ }

    // Snake bindings: use globals defined in page (dir, snake, playing, resetSnake, start, pause)
    (function(){
      var canvas = document.getElementById('snakeCanvas'); if(!canvas) return;
      function setDir(nd){
        try{
          if (typeof window.snake !== 'undefined' && typeof window.dir !== 'undefined'){
            if (window.snake.length>1 && (nd.x===-window.dir.x && nd.y===-window.dir.y)) return;
            window.dir = nd;
          } else if (typeof dir !== 'undefined') {
            if (window.snake && window.snake.length>1 && (nd.x===-dir.x && nd.y===-dir.y)) return;
            dir = nd;
          }
        }catch(_){ }
        try{ if(navigator.vibrate) navigator.vibrate(12); }catch(_){ }
      }
      function tap(id, fn){ var el=document.getElementById(id); if(!el) return; var h=function(ev){ ev.preventDefault(); fn(); }; el.addEventListener('touchstart', h, {passive:false}); el.addEventListener('click', h); }
      tap('snakeUp',    function(){ setDir({x:0,y:-1}); });
      tap('snakeDown',  function(){ setDir({x:0,y: 1}); });
      tap('snakeLeft',  function(){ setDir({x:-1,y:0}); });
      tap('snakeRight', function(){ setDir({x:1,y: 0}); });
      tap('snakeStartTouch', function(){ try{ if (typeof playing!=='undefined' && !playing){ resetSnake(); start(); } }catch(_){} });
      tap('snakePauseTouch', function(){ try{ if (typeof playing!=='undefined'){ if (playing) pause(); else start(); } }catch(_){} });
      // swipe one-turn
      var sx=0, sy=0, sid=null;
      canvas.addEventListener('touchstart', function(e){ var t=e.changedTouches[0]; sx=t.clientX; sy=t.clientY; sid=t.identifier; }, {passive:true});
      canvas.addEventListener('touchend', function(e){ var t=[].slice.call(e.changedTouches).find(function(x){ return x.identifier===sid; })||e.changedTouches[0]; if(!t) return; var dx=t.clientX-sx, dy=t.clientY-sy; sid=null; if(Math.hypot(dx,dy)<24) return; if(Math.abs(dx)>Math.abs(dy)) setDir({x:dx>0?1:-1,y:0}); else setDir({x:0,y:dy>0?1:-1}); }, {passive:true});
    })();

    // Duck bindings: dispatch keyboard events so existing handlers work
    (function(){
      function dispatchKey(k){ try{ window.dispatchEvent(new KeyboardEvent('keydown', {key:k, bubbles:true})); }catch(_){ } }
      function bindRepeatKey(id, key){ var el=document.getElementById(id); if(!el) return; var tid=null, rid=null; var start=function(ev){ ev.preventDefault(); dispatchKey(key); clearTimeout(tid); clearInterval(rid); tid=setTimeout(function(){ rid=setInterval(function(){ dispatchKey(key); }, 150); }, 220); }; var end=function(){ clearTimeout(tid); clearInterval(rid); tid=null; rid=null; }; ['touchstart','mousedown'].forEach(function(ev){ el.addEventListener(ev,start,{passive:false}); }); ['touchend','touchcancel','mouseup','mouseleave'].forEach(function(ev){ el.addEventListener(ev,end); }); }
      bindRepeatKey('duckLeft','ArrowLeft');
      bindRepeatKey('duckRight','ArrowRight');
      bindRepeatKey('duckUp','ArrowUp');
      bindRepeatKey('duckDown','ArrowDown');
      var pauseBtn=document.getElementById('duckPauseTouch'); if(pauseBtn){ ['touchstart','click'].forEach(function(ev){ pauseBtn.addEventListener(ev,function(e){ e.preventDefault(); dispatchKey('p'); }); }); }
      var startBtn=document.getElementById('duckStartTouch'); if(startBtn){ ['touchstart','click'].forEach(function(ev){ startBtn.addEventListener(ev,function(e){ e.preventDefault(); dispatchKey('p'); }); }); }
      // swipe on duck canvas
      var dc=document.getElementById('duckCanvas'); if(dc){ var sx=0, sy=0; dc.addEventListener('touchstart', function(e){ var t=e.changedTouches[0]; sx=t.clientX; sy=t.clientY; }, {passive:true}); dc.addEventListener('touchend', function(e){ var t=e.changedTouches[0]; var dx=t.clientX-sx, dy=t.clientY-sy; var adx=Math.abs(dx), ady=Math.abs(dy); if(Math.hypot(dx,dy)<24) return; dispatchKey(adx>ady ? (dx>0?'ArrowRight':'ArrowLeft') : (dy>0?'ArrowDown':'ArrowUp')); }, {passive:true}); }
    })();

    // Nothing extra for Jam; IDs preserved and script already supports hold.
  });
})();
