import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import PreviewWindowView from './views/PreviewWindowView.vue'
import router from './router'
import './styles/base.css'

function previewProps() {
  if (window.location.hash !== '#/preview') return undefined
  const params = new URLSearchParams(window.location.search)
  const name = params.get('name')?.trim()
  if (!name) return undefined
  const language = params.get('language') || 'zh-CN'
  return { name, language }
}

const preview = previewProps()
if (preview) {
  document.body.classList.add('preview-window-body')
  const theme = localStorage.getItem('nkas-theme')
  if (theme === 'dark' || theme === 'light') document.documentElement.dataset.theme = theme
  createApp(PreviewWindowView, preview).use(createPinia()).mount('#app')
} else {
  createApp(App).use(createPinia()).use(router).mount('#app')
}
