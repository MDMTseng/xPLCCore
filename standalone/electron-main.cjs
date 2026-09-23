// Minimal Electron shell for running the PackerX UI without the plugin host.
//
// The UI expects a renderer with Node integration: it opens raw TCP sockets
// with window.require('net'), spawns the feeder's Python bridge with
// child_process, and reads/writes files with fs. So: one window,
// nodeIntegration on, contextIsolation off. That is exactly what the old host
// provided, minus everything else it did.
//
// Started by standalone/run.cjs, which passes the dev-server URL in
// XPLC_UI_URL. Set XPLC_DEVTOOLS=1 to open DevTools.

const { app, BrowserWindow, powerSaveBlocker } = require('electron');

// This UI drives a machine: its timers (await delay(...), timeouts, the
// orchestration loop) must run at full rate whether or not the window is
// visible. Chromium's defaults clamp timers to 1 Hz once a window is hidden,
// minimised or covered (Windows occlusion detection counts "covered"), and
// harder still after 5 minutes. With the old host the UI only ran properly in
// the foreground. All of that is switched off here.
// XPLC_KEEP_THROTTLE=1 leaves Chromium's defaults on (for comparison only).
const unthrottled = process.env.XPLC_KEEP_THROTTLE !== '1';
if (unthrottled) {
  app.commandLine.appendSwitch('disable-background-timer-throttling');
  app.commandLine.appendSwitch('disable-renderer-backgrounding');
  app.commandLine.appendSwitch('disable-backgrounding-occluded-windows');
  app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion,IntensiveWakeUpThrottling');
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1600,
    height: 1000,
    title: 'PackerX UI (standalone)',
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      sandbox: false,
      backgroundThrottling: !unthrottled,   // same, per window
    },
  });
  // Keep the OS from suspending the process while the machine runs.
  powerSaveBlocker.start('prevent-app-suspension');
  win.loadURL(process.env.XPLC_UI_URL || 'http://localhost:5199/');
  if (process.env.XPLC_DEVTOOLS === '1') win.webContents.openDevTools({ mode: 'detach' });

  // XPLC_SNAPSHOT=<file.png>: save the window's content ~6 s after load and
  // quit -- lets a script check that the UI renders without a person looking.
  if (process.env.XPLC_SNAPSHOT) {
    win.webContents.once('did-finish-load', () => {
      setTimeout(async () => {
        const img = await win.webContents.capturePage();
        require('fs').writeFileSync(process.env.XPLC_SNAPSHOT, img.toPNG());
        console.log('[snapshot] saved', process.env.XPLC_SNAPSHOT);
        app.quit();
      }, 6000);
    });
  }

  // XPLC_THROTTLE_TEST=1: minimise the window, run a 50 ms interval for 5 s,
  // print the worst gap, quit. Throttled, the gap is ~1000 ms.
  if (process.env.XPLC_THROTTLE_TEST === '1') {
    win.webContents.once('did-finish-load', async () => {
      win.minimize();
      const r = await win.webContents.executeJavaScript(`new Promise((res) => {
        let last = performance.now(), worst = 0, n = 0;
        const id = setInterval(() => {
          const t = performance.now(); worst = Math.max(worst, t - last); last = t; n++;
        }, 50);
        setTimeout(() => { clearInterval(id); res({ ticks: n, worstGapMs: Math.round(worst) }); }, 5000);
      })`);
      console.log('[throttle-test] minimised, 50 ms interval over 5 s:', JSON.stringify(r));
      app.quit();
    });
  }
}

app.whenReady().then(createWindow);
app.on('window-all-closed', () => app.quit());
