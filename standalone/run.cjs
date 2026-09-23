// npm run standalone -- start the Vite dev server, then the Electron window.
// Closing the window stops both. Edits hot-reload.
//
//   XPLC_DEVTOOLS=1 npm run standalone     also open DevTools

const path = require('node:path');
const { spawn } = require('node:child_process');

(async () => {
  const repo = path.resolve(__dirname, '..');
  const { createServer } = await import('vite');
  const server = await createServer({ configFile: path.join(__dirname, 'vite.config.ts') });
  await server.listen();
  const url = server.resolvedUrls.local[0];
  console.log(`[standalone] UI at ${url}`);

  const electron = require('electron');   // path to the Electron binary
  const child = spawn(electron, [path.join(__dirname, 'electron-main.cjs')], {
    stdio: 'inherit',
    env: { ...process.env, XPLC_UI_URL: url, XPLC_REPO: repo },
  });
  child.on('exit', async (code) => {
    await server.close();
    process.exit(code ?? 0);
  });
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
