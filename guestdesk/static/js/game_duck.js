// ---------- Duck Crossing (Frogger-style) ----------
(() => {
  const panel = document.getElementById('panel-duck');
  if (!panel) return;

  const canvas = document.getElementById('duckCanvas');
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;

  const scoreEl = document.getElementById('duckScore');
  const livesEl = document.getElementById('duckLives');
  const msgEl   = document.getElementById('duckMsg');
  const btnStart= document.getElementById('duckStart');
  const btnPause= document.getElementById('duckPause');

  const TILE = 40; // grid size
  const COLS = Math.floor(W/TILE), ROWS = Math.floor(H/TILE);
  const START_POS = { x: Math.floor(COLS/2), y: ROWS-1 };

  let keys = {}, running=false, raf;
  let duck, lanes, score, lives, level;

  function reset(full=true){
    level = full ? 1 : level+1;
    score = full ? 0 : score;
    lives = full ? 3 : lives;
    duck = {...START_POS};
    buildLanes();
    updateHUD();
    msgEl.textContent = full ? "Get the duck to the pond!" : `Level ${level}!`;
    draw();
  }
  function updateHUD(){ scoreEl.textContent = score; livesEl.textContent = lives; }

  function buildLanes(){
    // pond row (0-1), then 5 traffic lanes across rows 2..6, bottom rows are grass
    lanes = [];
    const speeds = [1.8, 2.3, 2.8, 3.2, 3.8].map(s=> s + (level-1)*0.35);
    for (let i=0;i<5;i++){
      const row = 2 + i; // rows 2..6
      const dir = i%2===0 ? 1 : -1;
      lanes.push({ row, dir, speed: speeds[i], cars: spawnCars(dir, speeds[i]) });
    }
  }

  function spawnCars(dir, speed){
    const list = [];
    for (let i=0;i<6;i++){
      const w = 60 + Math.random()*50;
      const gap = 100 + Math.random()*120;
      const baseX = Math.random()*W;
      list.push({ x: baseX + i*(w+gap), w, h: 28, dir, speed, color: carColor() });
    }
    return list;
  }

  function carColor(){
    const palette = ["#f87171","#60a5fa","#fbbf24","#34d399","#a78bfa","#fb7185","#22d3ee"];
    return palette[Math.floor(Math.random()*palette.length)];
  }

  function tick(){
    // move lanes
    for (const lane of lanes){
      for (const c of lane.cars){
        c.x += c.dir * lane.speed;
        if (lane.dir > 0 && c.x - c.w > W) c.x = -Math.random()*200;
        if (lane.dir < 0 && c.x + c.w < 0)  c.x = W + Math.random()*200;
      }
    }

    // collisions
    const duckRect = { x: duck.x*TILE+6, y: duck.y*TILE+6, w: TILE-12, h: TILE-12 };
    if (duck.y >= 2 && duck.y <= 6){
      const lane = lanes.find(l => l.row === duck.y);
      if (lane){
        for (const c of lane.cars){
          const carRect = { x: c.x - c.w/2, y: duck.y*TILE + 6, w: c.w, h: 28 };
          if (intersect(duckRect, carRect)){
            lives--; updateHUD();
            msgEl.textContent = "Bonk! Watch for traffic.";
            duck = {...START_POS};
            if (lives <= 0){ gameOver(); return; }
          }
        }
      }
    }

    draw();
    raf = requestAnimationFrame(tick);
  }

  function intersect(a,b){
    return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  }

  function draw(){
    ctx.clearRect(0,0,W,H);

    // background bands
    // top pond
    ctx.fillStyle = "#0a2438"; ctx.fillRect(0,0,W,TILE*2);
    // middle road
    ctx.fillStyle = "#161a34"; ctx.fillRect(0,TILE*2,W,TILE*5);
    // bottom grass
    ctx.fillStyle = "#0f172a"; ctx.fillRect(0,TILE*7,W, H - TILE*7);

    // road markings
    ctx.strokeStyle = "#9aa3b248"; ctx.setLineDash([10, 10]); ctx.lineWidth = 2;
    for (let r=2; r<=6; r++){
      ctx.beginPath();
      ctx.moveTo(0, r*TILE + TILE/2); ctx.lineTo(W, r*TILE + TILE/2);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // pond ripples
    ctx.strokeStyle = "#7cf"; ctx.globalAlpha = 0.25;
    for (let i=0;i<6;i++){
      ctx.beginPath();
      ctx.arc(W/2, TILE, 30+i*18, 0, Math.PI*2);
      ctx.stroke();
    }
    ctx.globalAlpha = 1;

    // cars
    for (const lane of lanes){
      const y = lane.row*TILE + TILE/2;
      for (const c of lane.cars){
        ctx.fillStyle = c.color;
        ctx.fillRect(c.x - c.w/2, y-14, c.w, 28);
        // windshield
        ctx.fillStyle = "#e8eaf6";
        const wx = lane.dir>0 ? (c.x + c.w/2 - 18) : (c.x - c.w/2 + 6);
        ctx.fillRect(wx, y-10, 12, 20);
      }
    }

    // duck
    drawDuck(duck.x*TILE + TILE/2, duck.y*TILE + TILE/2);
  }

  function drawDuck(cx, cy){
    // body
    ctx.fillStyle = "#fbbf24";
    ctx.beginPath(); ctx.ellipse(cx, cy, 14, 10, 0, 0, Math.PI*2); ctx.fill();
    // head
    ctx.beginPath(); ctx.arc(cx+12, cy-6, 7, 0, Math.PI*2); ctx.fill();
    // beak
    ctx.fillStyle = "#fb7185";
    ctx.fillRect(cx+17, cy-8, 8, 4);
    // eye
    ctx.fillStyle = "#0e1330";
    ctx.fillRect(cx+13, cy-9, 2, 2);
    // feet
    ctx.fillStyle = "#fb7185";
    ctx.fillRect(cx-6, cy+8, 4, 4); ctx.fillRect(cx+2, cy+8, 4, 4);
  }

  // grid-step movement (only when running)
  function move(dx, dy){
    const nx = Math.max(0, Math.min(COLS-1, duck.x + dx));
    const ny = Math.max(0, Math.min(ROWS-1, duck.y + dy));
    duck.x = nx; duck.y = ny;

    // reached pond (top rows)
    if (duck.y <= 1){
      score += 100 * level;
      msgEl.textContent = "Quack! You made it!";
      updateHUD();
      reset(false); // next level
    }
  }

  function start(){
    if (running) return;
    running = true; msgEl.textContent = "";
    raf = requestAnimationFrame(tick);
  }
  function pause(){
    if (!running) return;
    running = false; cancelAnimationFrame(raf);
    msgEl.textContent = "Paused.";
  }
  function gameOver(){
    pause();
    msgEl.textContent = "All lives lost — press Start to try again.";
    reset(true);
  }

  // Input only when panel is active
  window.addEventListener('keydown', (e)=>{
    if (!panel.classList.contains('active')) return;
    if (["ArrowLeft","ArrowRight","ArrowUp","ArrowDown","a","d","w","s","p","P"].includes(e.key)) e.preventDefault();
    if (e.key==="p"||e.key==="P"){ running ? pause() : start(); return; }
    if (!running) return;
    if (e.key==="ArrowLeft"||e.key==="a")  move(-1,0);
    if (e.key==="ArrowRight"||e.key==="d") move(1,0);
    if (e.key==="ArrowUp"||e.key==="w")    move(0,-1);
    if (e.key==="ArrowDown"||e.key==="s")  move(0,1);
  });

  btnStart.onclick = ()=>{ if (!running){ start(); } };
  btnPause.onclick = ()=>{ running ? pause() : start(); };

  // init
  score = 0; lives = 3; level = 1; reset(true); draw();
})();

