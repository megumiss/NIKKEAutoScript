import { computed } from 'vue'
import { useRoute } from 'vue-router'

// 路由派生的页面判定，壳组件与视图共用。
export function useRouteInfo() {
  const route = useRoute()
  const selectedName = computed(() => String(route.params.name || ''))
  const selectedPage = computed(() => String(route.params.page || ''))
  const selectedTask = computed(() => String(route.params.task || ''))
  const isDashboard = computed(() => route.path === '/')
  const isManage = computed(() => route.path === '/manage')
  const isSettings = computed(() => route.path === '/settings')
  const isDeploy = computed(() => route.path === '/deploy')
  const isLogs = computed(() => route.path === '/logs')
  const isLinks = computed(() => route.path === '/links')
  const isAbout = computed(() => route.path === '/about')
  const isWorkspace = computed(() => Boolean(selectedName.value))
  return { selectedName, selectedPage, selectedTask, isDashboard, isManage, isSettings, isDeploy, isLogs, isLinks, isAbout, isWorkspace }
}
