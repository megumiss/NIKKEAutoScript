import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  base: '/app/',
  build: {
    target: 'chrome94',
    sourcemap: false,
    // Wipe stale hashed assets before building; dist is committed to git, so
    // leaving them would accumulate dead bundles in the repo.
    emptyOutDir: true,
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
