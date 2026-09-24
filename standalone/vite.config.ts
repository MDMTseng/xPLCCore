// Dev server for the standalone UI (app mode, not the library build in
// ../vite.config.ts). Node builtins are reached through window.require at
// runtime, so nothing here needs to know about them.
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  root: __dirname,
  plugins: [react({ jsxRuntime: 'classic' })],
  server: {
    port: 5199,
    strictPort: true,
    fs: { allow: [path.resolve(__dirname, '..')] },
    // Harness runs (tools/sim/run_virtual.py sets XPLC_HARNESS) must not
    // hot-reload: an edit saved mid-run replaced CalibPage under a running
    // cycle and broke it ("left over promise", 2026-09-25). Code changes
    // apply on the next run, which starts a fresh UI anyway.
    hmr: process.env.XPLC_HARNESS ? false : undefined,
    watch: process.env.XPLC_HARNESS ? { ignored: ['**/*'] } : undefined,
  },
  resolve: { dedupe: ['react', 'react-dom'] },
});
