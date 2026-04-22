import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import dts from 'vite-plugin-dts'
import cssInjectedByJsPlugin from 'vite-plugin-css-injected-by-js'

export default defineConfig({
  plugins: [
    react({ jsxRuntime: 'classic' }),
    cssInjectedByJsPlugin(),
    dts({ outDir: 'dist', insertTypesEntry: true })
  ],
  build: {
    lib: {
      entry: 'index.tsx',
      name: 'BasePluginUI',
      fileName: (format) => (format === 'cjs' ? 'index.cjs' : `index.${format}.js`),
      formats: ['es', 'cjs', 'iife']
    },
    rollupOptions: {
      external: ['react', 'react-dom', 'react/jsx-runtime'],
      output: {
        globals: {
          react: 'React',
          'react-dom': 'ReactDOM',
          'react/jsx-runtime': 'jsxRuntime'
        }
      }
    },
    sourcemap: true,
    emptyOutDir: true,
    minify: 'esbuild'
  }
})


