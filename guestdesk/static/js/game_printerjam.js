// ---------- Printer Jam Escape (Breakout) ----------
(() => {
  const panel = document.getElementById('panel-printerjam');
  if (!panel) return;

  const canvas = document.getElementById('jamCanvas');
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;

  const levelEl = document.getElementById('jamLevel');
  const scoreEl = document.getElementById('jamScore');
  const livesEl = document.getElementById('jamLives');
  const msgEl   = document.getElementById('jamMsg');
  const btnStart= document.getElementById('jamStart');
  const btnPause= document.getElementById('jamPause');

  let keys = {}, running=false, raf;
  let ball, paddle, bricks, rows, cols, score, lives, level, speed;

  const PADDING = 16, GAP=6, BRICK_H=22;
  function brickGeom(cols){
    const usable = W - PADDING*2 - GAP*(cols-1);
    const bw = Math.floor(usable/cols);
    const bx = PADDING, by = 60; // header space
    return { bw, bx, by };
  }

  function reset(full=true){
    level   = full ? 1 : level+1;
    speed   = 4 + (level-1)*0.4;
    rows    = 5 + Math.min(level-1, 3);
    cols    = 10;
    const {bw,bx,by} = brickGeom(cols);

    // bricks: some “JAM” bricks (2 hits)
    bricks = [];
    for (let r=0;r<rows;r++){
      for (let c=0;c<cols;c++){
        const jam = Math.random() < (0.18 + level*0.02);
        bricks.push({
          x: bx + c*(bw+GAP),
          y: by + r*(BRICK_H+GAP),
          w: bw, h: BRICK_H,
          hits: jam ? 2 : 1,
          jam
        });
      }
    }
    const padW = 110 - Math.min((level-1)*8, 60);
    paddle = { w: padW, h: 12, x: W/2-padW/2, y: H-40, vx: 0 };
    ball   = { x: W/2, y: H-60, r: 7, vx: speed*(Math.random()<0.5?-1:1), vy: -speed };

    if (full){ score = 0; lives = 3; }
    updateHUD();
    msgEl.textContent = "Clear the jams!";
    draw();
  }

  function updateHUD(){
    levelEl.textContent = level;
    scoreEl.textContent = score;
    livesEl.textContent = lives;
  }

  function input(){
    const accel = 0.9, maxv = 8;
    if (keys["ArrowLeft"] || keys["a"])      paddle.vx = Math.max(paddle.vx-accel, -maxv);
    else if (keys["ArrowRight"] || keys["d"])paddle.vx = Math.min(paddle.vx+accel,  maxv);
    else                                     paddle.vx *= 0.82;

    paddle.x += paddle.vx;
    if (paddle.x < PADDING)                          { paddle.x = PADDING; paddle.vx = 0; }
    if (paddle.x + paddle.w > W-PADDING)             { paddle.x = W-PADDING-paddle.w; paddle.vx = 0; }
  }

  function tick(){
    input();
    // move ball
    ball.x += ball.vx; ball.y += ball.vy;

    // walls
    if (ball.x < ball.r)       { ball.x = ball.r;       ball.vx *= -1; }
    if (ball.x > W-ball.r)     { ball.x = W-ball.r;     ball.vx *= -1; }
    if (ball.y < ball.r)       { ball.y = ball.r;       ball.vy *= -1; }

    // paddle
    if (ball.y+ball.r >= paddle.y && ball.y+ball.r <= paddle.y + paddle.h + 6 &&
        ball.x >= paddle.x && ball.x <= paddle.x + paddle.w){
      // reflect with “english”
      const hit = (ball.x - (paddle.x + paddle.w/2)) / (paddle.w/2);
      const ang = hit * (Math.PI/3); // -60..+60°
      const spd = Math.hypot(ball.vx, ball.vy)*1.02;
      ball.vx = spd * Math.sin(ang);
      ball.vy = -Math.abs(spd * Math.cos(ang));
      ball.y  = paddle.y - ball.r - 0.1;
    }

    // bricks
    for (let i=0;i<bricks.length;i++){
      const b = bricks[i]; if (!b) continue;
      if (ball.x > b.x && ball.x < b.x+b.w && ball.y > b.y && ball.y < b.y+b.h){
        const prevX = ball.x - ball.vx, prevY = ball.y - ball.vy;
        const fromLeft   = prevX <= b.x;
        const fromRight  = prevX >= b.x+b.w;
        // reflect using side we came from
        if (fromLeft || fromRight) ball.vx *= -1; else ball.vy *= -1;

        b.hits--;
        score += b.jam ? 20 : 10;
        if (b.hits <= 0) bricks[i] = null;
        break;
      }
    }
    bricks = bricks.filter(Boolean);

    // fell?
    if (ball.y - ball.r > H){
      lives--; updateHUD();
      if (lives <= 0){ gameOver(); return; }
      paddle.x = W/2 - paddle.w/2; paddle.vx = 0;
      ball.x = W/2; ball.y = H-60; ball.vx = speed*(Math.random()<0.5?-1:1); ball.vy = -speed;
      msgEl.textContent = "Life lost. Press Space/Start.";
      pause();
    }

    // level clear
    if (bricks.length===0){
      msgEl.textContent = "Paper path cleared! Next level…";
      pause(); reset(false); start(); return;
    }

    draw();
    raf = requestAnimationFrame(tick);
  }

  function draw(){
    ctx.clearRect(0,0,W,H);
    // header strip
    ctx.fillStyle = "#0b0f28"; ctx.fillRect(0,0,W,48);
    ctx.fillStyle = "#9aa3b2"; ctx.fillRect(0,48,W,2);

    // bricks
    for (const b of bricks){
      ctx.fillStyle = b.jam ? "#f87171" : "#e8eaf6";
      ctx.fillRect(b.x, b.y, b.w, b.h);
      if (!b.jam){
        // lines on paper bricks
        ctx.strokeStyle = "#cfd2e2"; ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(b.x+6, b.y+6); ctx.lineTo(b.x+b.w-6, b.y+6);
        ctx.moveTo(b.x+6, b.y+b.h-6); ctx.lineTo(b.x+b.w-6, b.y+b.h-6);
        ctx.stroke();
      } else {
        ctx.fillStyle = "#0e1330";
        ctx.font = "bold 12px system-ui"; ctx.textAlign="center"; ctx.textBaseline="middle";
        ctx.fillText("JAM", b.x + b.w/2, b.y + b.h/2);
      }
    }

    // paddle
    ctx.fillStyle = "#7cf"; ctx.fillRect(paddle.x, paddle.y, paddle.w, paddle.h);

    // ball
    ctx.fillStyle = "#fbbf24"; ctx.beginPath(); ctx.arc(ball.x, ball.y, ball.r, 0, Math.PI*2); ctx.fill();
  }

  function start(){ if (running) return; running=true; msgEl.textContent=""; raf=requestAnimationFrame(tick); }
  function pause(){ if (!running) return; running=false; cancelAnimationFrame(raf); msgEl.textContent = msgEl.textContent||"Paused."; }
  function gameOver(){ pause(); msgEl.textContent = "Out of paper lives! Press Start to restart."; reset(true); }

  // Input only when panel visible
  window.addEventListener('keydown', (e)=>{
    if (!panel.classList.contains('active')) return;
    if (["ArrowLeft","ArrowRight"," ","a","d"].includes(e.key)) e.preventDefault();
    keys[e.key] = true;
    if (e.key===" ") running ? pause() : start();
  });
  window.addEventListener('keyup', (e)=>{ keys[e.key] = false; });

  // Controls
  btnStart.onclick = ()=>{ if (!running) { if (msgEl.textContent.includes("restart")) reset(true); start(); } };
  btnPause.onclick = ()=>{ running ? pause() : start(); };

  // init
  reset(true); draw();
})();

