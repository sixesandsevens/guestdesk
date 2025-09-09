// Lightweight first-party analytics collector
(function(){
  try {
    if (navigator.doNotTrack === '1' || window.doNotTrack === '1') return;
    // avoid self-inflation: skip admin analytics page itself
    if ((location.pathname || '').startsWith('/admin/analytics')) return;

    function uuid(){
      return (crypto.randomUUID ? crypto.randomUUID() :
        'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,function(c){var r=Math.random()*16|0,v=c=='x'?r:(r&0x3|0x8);return v.toString(16);}));
    }

    // anon id cookie (gd_anon) and session id in sessionStorage (gd_sid)
    function getCookie(name){
      var m = document.cookie.match(new RegExp('(?:^|;\\s*)'+name+'=([^;]+)')); return m?m[1]:null;
    }
    var anon = getCookie('gd_anon');
    if (!anon) { anon = uuid(); var exp = new Date(); exp.setFullYear(exp.getFullYear()+1); document.cookie = 'gd_anon='+anon+'; path=/; expires='+exp.toUTCString()+'; samesite=Lax'; }
    if (!sessionStorage.getItem('gd_sid')) sessionStorage.setItem('gd_sid', uuid());
    var sid = sessionStorage.getItem('gd_sid');

    // device/os/browser quick sniff
    function deviceType(){ var u=navigator.userAgent.toLowerCase(); if(/bot|crawl|spider|slurp|facebookexternalhit/.test(u)) return 'bot'; if(/tablet|ipad/.test(u)) return 'tablet'; if(/mobi|iphone|android/.test(u)) return 'mobile'; return 'pc'; }
    function osName(){ var u=navigator.userAgent; if(/Windows/i.test(u)) return 'Windows'; if(/Mac OS X/i.test(u)) return 'macOS'; if(/Android/i.test(u)) return 'Android'; if(/iPhone|iPad|iOS/i.test(u)) return 'iOS'; if(/Linux/i.test(u)) return 'Linux'; return 'Other'; }
    function browserName(){ var u=navigator.userAgent; if(/Edg\//.test(u)) return 'Edge'; if(/Chrome\//.test(u)) return 'Chrome'; if(/Safari\//.test(u) && !/Chrome\//.test(u)) return 'Safari'; if(/Firefox\//.test(u)) return 'Firefox'; return 'Other'; }
    function refPath(){ try{ var r=document.referrer; if(!r) return null; var u=new URL(r); return u.pathname + (u.search||''); }catch(e){ return null; } }
    function pageLoadMs(){ try{ var n=performance.getEntriesByType('navigation')[0]; if(n && n.loadEventEnd) return Math.round(n.loadEventEnd); var t=performance.timing; return Math.max(0, t.loadEventEnd - t.navigationStart); }catch(e){ return 0; } }

    var start = Date.now();
    function payload(extra){
      return Object.assign({
        client_id: anon,
        anon_id: anon,
        session_id: sid,
        path: location.pathname + location.search,
        referrer: document.referrer || null,
        referrer_path: refPath(),
        started_at_ms: start,
        ended_at_ms: Date.now(),
        device: deviceType(), os: osName(), browser: browserName(),
        page_load_ms: pageLoadMs()
      }, extra||{});
    }

    function send(extra){
      var body = JSON.stringify(payload(extra));
      try { navigator.sendBeacon('/analytics/collect', new Blob([body], {type:'application/json'})); }
      catch(e){ fetch('/analytics/collect', {method:'POST', headers:{'Content-Type':'application/json'}, body}); }
    }

    // initial pageview after load to capture load time
    window.addEventListener('load', function(){ setTimeout(function(){ send({action:'view', category:'page'}); }, 0); });
    // on hide, send final duration
    document.addEventListener('visibilitychange', function(){ if (document.visibilityState === 'hidden') send({action:'view', category:'page'}); });
    window.addEventListener('pagehide', function(){ send({action:'view', category:'page'}); });

    // expose helpers
    window.GDAnalytics = {
      formSubmit: function(name){ send({action:'submit', category:'form', label: String(name||'')}); },
      funPlay: function(name){ send({action:'play', category:'funzone', label: String(name||'')}); }
    };
  } catch (_) {}
})();
