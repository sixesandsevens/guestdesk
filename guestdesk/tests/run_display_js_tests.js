/* Run with: node guestdesk/tests/run_display_js_tests.js */
const fs = require('fs');
const path = require('path');
const write = process.stdout.write.bind(process.stdout);
const root = path.resolve(__dirname, '..');

async function run(harness, source) {
  const test = eval(fs.readFileSync(path.join(__dirname, harness), 'utf8'));
  const code = fs.readFileSync(path.join(root, 'static', 'js', source), 'utf8');
  write(`${await test(code)}\n`);
}

(async () => {
  await run('display_player_harness.js', 'display_player.js');
  await run('display_youtube_harness.js', 'display_youtube.js');
})().catch(error => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
