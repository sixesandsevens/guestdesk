// Funzone mobile helper
// Keeps backwards-compatibility with older cached markup by injecting
// touch controls when they are missing and adds light haptic feedback on tap.
(function(){
  function onReady(fn){
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  function vibrate(ms){
    try {
      if (navigator.vibrate) navigator.vibrate(ms);
    } catch (_){ /* noop */ }
  }

  function ensureSnakeControls(){
    var panel = document.getElementById('panel-snake');
    if (!panel) return;

    // If the template already provides the modern layout, nothing to do.
    var pad = panel.querySelector('.touchpad');
    var bar = panel.querySelector('.touchbar');
    if (!pad){
      pad = document.createElement('div');
      pad.className = 'touchpad';
      pad.setAttribute('aria-hidden', 'true');
      pad.innerHTML = ''+
        '<span></span><button class="btn" id="snakeUp" aria-label="Up">↑</button><span></span>'+
        '<button class="btn" id="snakeLeft" aria-label="Left">←</button>'+
        '<button class="btn" id="snakeDown" aria-label="Down">↓</button>'+
        '<button class="btn" id="snakeRight" aria-label="Right">→</button>';
      var wrap = panel.querySelector('.game-wrap');
      if (!wrap){
        wrap = document.createElement('div');
        wrap.className = 'game-wrap';
        var canvas = panel.querySelector('canvas');
        if (canvas && canvas.parentElement){
          canvas.parentElement.insertBefore(wrap, canvas);
          wrap.appendChild(canvas);
        } else {
          wrap = null;
        }
      }
      if (wrap) wrap.appendChild(pad);
    }
    if (!bar){
      bar = document.createElement('div');
      bar.className = 'touchbar';
      bar.setAttribute('aria-hidden','true');
      bar.innerHTML = ''+
        '<button class="btn" id="snakeStartTouch">Start</button>'+
        '<button class="btn" id="snakePauseTouch">Pause</button>';
      (panel.querySelector('.game-wrap') || panel).appendChild(bar);
    }

    // Add subtle haptic feedback for taps.
    var targets = panel.querySelectorAll('.touchpad button, .touchbar button');
    targets.forEach(function(btn){
      btn.addEventListener('touchstart', function(){ vibrate(15); }, {passive:true});
      btn.addEventListener('click', function(){ vibrate(10); });
    });
  }

  function enhancePrinterJam(){
    var panel = document.getElementById('panel-printerjam');
    if (!panel) return;
    var bar = panel.querySelector('.touchbar');
    if (!bar) return;
    var buttons = bar.querySelectorAll('button');
    buttons.forEach(function(btn){
      btn.addEventListener('touchstart', function(){ vibrate(12); }, {passive:true});
      btn.addEventListener('click', function(){ vibrate(8); });
    });
  }

  onReady(function(){
    ensureSnakeControls();
    enhancePrinterJam();
  });
})();
