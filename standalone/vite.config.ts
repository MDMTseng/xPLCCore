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
  },
  resolve: { dedupe: ['react', 'react-dom'] },
});
