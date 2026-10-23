import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import path from 'path'

const backend = process.env.BACKEND_URL || 'http://127.0.0.1:8010'

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { '@': path.resolve(__dirname, 'src') } },
  server: {
    port: 3000,
    proxy: Object.fromEntries(['/api', '/media', '/m/', '/oauth', '/health'].map((p) => [p, { target: backend, changeOrigin: true }])),
  },
  build: { chunkSizeWarningLimit: 1500 },
})
