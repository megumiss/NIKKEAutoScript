import { createRouter, createWebHashHistory } from 'vue-router'

// 所有路由都挂到 App 壳组件，由 App 按路由计算属性切换视图；URL 行为保持不变。
// App 必须懒加载：App.vue → stores → router.ts 存在循环引用，静态 import 会让
// createRouter 在 App 模块初始化之前求值（TDZ），页面直接白屏。
const App = () => import('./App.vue')

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: App },
    { path: '/i/:name', redirect: to => `/i/${to.params.name}/overview` },
    { path: '/i/:name/:page(overview|task|tool|schedule)/:task?', component: App },
    { path: '/manage', component: App },
    { path: '/settings', component: App },
    { path: '/deploy', component: App },
    { path: '/logs', component: App },
    { path: '/links', component: App },
    { path: '/about', component: App },
  ],
})

export default router
