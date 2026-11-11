import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    // 固定绑定 IPv4，避免只监听 [::1] 导致 http://127.0.0.1:5173 打不开
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      // 前端不直接接触 LLM API：所有请求都走后端
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
})
