import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/app/',
  build: {
    target: 'chrome94',
    sourcemap: false,
    // Keep the out dir as-is before building; stale hashed assets are pruned
    // separately so builds don't depend on removing the previous output.
    emptyOutDir: false,
    rollupOptions: {
      output: {
        manualChunks: {
          vue: ['vue', 'vue-router', 'pinia'],
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:12271',
      '/ws': { target: 'ws://127.0.0.1:12271', ws: true },
    },
  },
})
