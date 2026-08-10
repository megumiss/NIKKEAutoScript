import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import App from './App.vue'
import './styles/base.css'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: App },
    { path: '/i/:name', redirect: to => `/i/${to.params.name}/overview` },
    { path: '/i/:name/:page(overview|task|tool)/:task?', component: App },
    { path: '/manage', component: App },
    { path: '/settings', component: App },
    { path: '/deploy', component: App },
    { path: '/logs', component: App },
    { path: '/links', component: App },
    { path: '/about', component: App },
  ],
})

createApp(App).use(router).mount('#app')
