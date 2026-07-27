import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import './styles/base.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: App },
    { path: '/i/:name/:page(overview|task|tool)/:task?', component: App },
    { path: '/manage', component: App },
    { path: '/settings', component: App },
    { path: '/about', component: App },
  ],
})

createApp(App).use(createPinia()).use(router).mount('#app')
