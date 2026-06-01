import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react-swc'

export default defineConfig({
  plugins: [react()],

  server: {
    port: 5173,
    host: true,
    strictPort: false,

    proxy: {
      '/auth':         { target: 'http://localhost:8000', changeOrigin: true },
      '/signals':      { target: 'http://localhost:8000', changeOrigin: true },
      '/trades':       { target: 'http://localhost:8000', changeOrigin: true },
      '/tradelog':     { target: 'http://localhost:8000', changeOrigin: true },
      '/analytics':    { target: 'http://localhost:8000', changeOrigin: true },
      '/paper':        { target: 'http://localhost:8000', changeOrigin: true },
      '/subscription': { target: 'http://localhost:8000', changeOrigin: true },
      '/chart':        { target: 'http://localhost:8000', changeOrigin: true },
      '/indices':      { target: 'http://localhost:8000', changeOrigin: true },
      '/movers':       { target: 'http://localhost:8000', changeOrigin: true },
      '/health':       { target: 'http://localhost:8000', changeOrigin: true },
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
        changeOrigin: true,
        // Prevent ECONNABORTED — give backend time to start up
        proxyTimeout: 10000,
        timeout: 10000,
        configure: (proxy) => {
          proxy.on('error', (err) => {
            // Suppress noisy ECONNABORTED/ECONNREFUSED — frontend handles reconnect
            if (!['ECONNABORTED','ECONNREFUSED','ECONNRESET'].includes(err.code)) {
              console.error('[WS Proxy Error]', err.message)
            }
          })
        },
      },
    },
  },

  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 2000,
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom'],
          charts: ['recharts'],
        },
      },
    },
  },
})
