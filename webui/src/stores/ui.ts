import { ref } from 'vue'
import { defineStore } from 'pinia'
import router from '../router'

// 移动端抽屉导航：'' = 关闭，'sidebar' = 总导航抽屉，'rail' = 任务列表抽屉。
// 桌面端（>768px）此状态无效果，布局保持不变。
export const useUiStore = defineStore('ui', () => {
  const mobileNav = ref<'sidebar' | 'rail' | ''>('')
  const sidebarCollapsed = ref(false)

  function dashboard() { mobileNav.value = ''; router.push('/') }
  function enter(name: string) { mobileNav.value = ''; router.push(`/i/${name}/overview`) }

  return { mobileNav, sidebarCollapsed, dashboard, enter }
})
