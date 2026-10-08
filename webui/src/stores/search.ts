import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import router from '../router'
import { useInstancesStore } from './instances'
import { useWorkspaceStore } from './workspace'

// 全站入口注册表：路由与文案都留在 SPA（后端不认识这些路由），后端只提供任务/
// 设置的目录。instance 为 true 的条目要先确定目标实例，再替换路径里的占位符。
export type SearchPage = { key: string; label: string; group: string; path: string; instance?: boolean }

const PAGES: SearchPage[] = [
  { key: 'dashboard', label: '总览', group: '系统', path: '/' },
  { key: 'manage', label: '多开', group: '系统', path: '/manage' },
  { key: 'deploy', label: '部署', group: '系统', path: '/deploy' },
  { key: 'logs', label: '日志', group: '系统', path: '/logs' },
  { key: 'settings', label: '更新', group: '系统', path: '/settings' },
  { key: 'about', label: '关于', group: '系统', path: '/about' },
  { key: 'tools', label: '常用工具', group: '其他', path: '/tools' },
  { key: 'tools-hosts', label: 'Hosts 修改', group: '常用工具', path: '/tools/hosts' },
  { key: 'tools-shortcuts', label: '快捷键设置', group: '常用工具', path: '/tools/shortcuts' },
  { key: 'tools-clone', label: '游戏多开', group: '常用工具', path: '/tools/clone' },
  { key: 'tools-driver', label: '虚拟鼠标驱动', group: '常用工具', path: '/tools/driver' },
  { key: 'tools-console', label: '控制台', group: '常用工具', path: '/tools/console' },
  { key: 'links', label: '常用链接', group: '其他', path: '/links' },
  { key: 'overview', label: '任务总览', group: '实例页面', path: '/i/{instance}/overview', instance: true },
  { key: 'schedule', label: '调度设置', group: '实例页面', path: '/i/{instance}/schedule', instance: true },
]

const DEBOUNCE_MS = 160
// 后端上限 100；一次取满上限，「+N」只在总命中超过上限时出现。
const SEARCH_LIMIT = 100
// 跨实例跳转要先等目标实例的 schema 加载完才渲染出字段，比实例内跳转留更长的重试窗口。
const SCROLL_TRIES = 40

export type SearchHit = {
  kind: 'task' | 'field'
  task: string
  task_name: string
  title: string
  help: string
  menu: string
  menu_name: string
  page: string
  group: string | null
  group_name: string | null
  field: string | null
}

export const useSearchStore = defineStore('search', () => {
  const instancesStore = useInstancesStore()
  const workspace = useWorkspaceStore()

  const open = ref(false)
  const query = ref('')
  const loading = ref(false)
  const settings = ref<SearchHit[]>([])
  const total = ref(0)
  const error = ref('')

  let timer: number | undefined
  // 每次请求带一个序号，慢响应回来时丢弃，避免覆盖更新的一次输入。
  let seq = 0

  const instanceNames = computed(() => instancesStore.instances.map(item => item.name))
  // 命中的任务/设置本身与实例无关（名字来自静态 schema，只有值分实例），所以结果
  // 只带一个默认目标实例：优先留在用户当前所在实例，否则回落到上次加载过的实例。
  const targetInstance = computed(() => workspace.selectedName || workspace.workspaceName || instanceNames.value[0] || '')
  const multiInstance = computed(() => instanceNames.value.length > 1)

  const pageHits = computed(() => {
    const q = query.value.trim().toLowerCase()
    if (!q) return []
    // 中文原文也参与匹配，英文界面下输入中文同样能命中。
    return PAGES.filter(page => `${page.label} ${t(page.label)} ${t(page.group)}`.toLowerCase().includes(q))
  })

  // 多实例时同一批命中按实例分组展示，每组指向各自的实例；单实例只有一组。
  const hitGroups = computed(() => {
    if (!settings.value.length) return [] as { name: string; hits: SearchHit[] }[]
    if (!multiInstance.value) return [{ name: '', hits: settings.value }]
    return instanceNames.value.map(name => ({ name, hits: settings.value }))
  })

  // 键盘导航用的扁平列表，顺序与面板渲染顺序一致：页面在前，命中分组在后。
  type SearchItem = { type: 'page'; page: SearchPage } | { type: 'hit'; hit: SearchHit; instance: string }
  const items = computed<SearchItem[]>(() => {
    const list: SearchItem[] = pageHits.value.map(page => ({ type: 'page', page }))
    for (const group of hitGroups.value) {
      for (const hit of group.hits) list.push({ type: 'hit', hit, instance: group.name })
    }
    return list
  })
  const activeIndex = ref(0)
  watch(items, () => { activeIndex.value = 0 })
  function moveActive(delta: number) {
    if (!items.value.length) return
    activeIndex.value = (activeIndex.value + delta + items.value.length) % items.value.length
  }

  function reset() {
    query.value = ''
    settings.value = []
    total.value = 0
    error.value = ''
  }
  function close() {
    window.clearTimeout(timer)
    open.value = false
    reset()
  }
  function toggle() { open.value ? close() : (open.value = true) }

  async function run() {
    const q = query.value.trim()
    if (!q) return
    const token = ++seq
    loading.value = true
    try {
      const data = await api.get(`/api/search?q=${encodeURIComponent(q)}&limit=${SEARCH_LIMIT}`)
      if (token !== seq) return
      settings.value = data.settings || []
      total.value = data.total || 0
      error.value = ''
    } catch (exception: any) {
      if (token !== seq) return
      settings.value = []
      total.value = 0
      error.value = exception.message
    } finally {
      if (token === seq) loading.value = false
    }
  }
  function schedule() {
    window.clearTimeout(timer)
    if (!query.value.trim()) {
      seq++
      loading.value = false
      settings.value = []
      total.value = 0
      error.value = ''
      return
    }
    loading.value = true
    timer = window.setTimeout(run, DEBOUNCE_MS)
  }

  function openPage(page: SearchPage) {
    if (page.instance && !targetInstance.value) return
    const path = page.instance ? page.path.replace('{instance}', targetInstance.value) : page.path
    close()
    router.push(path)
  }

  function openHit(hit: SearchHit, instance?: string) {
    const name = instance || targetInstance.value
    if (!name) return
    close()
    router.push(`/i/${name}/${hit.page === 'tool' ? 'tool' : 'task'}/${hit.task}`)
    if (!hit.field) return
    // 与任务栏筛选一致的定位方式：等目标页渲染出字段后再滚动并高亮。
    const id = `field-${hit.field}`
    let tries = 0
    const scrollToField = () => {
      const el = document.getElementById(id)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
        el.classList.add('field-flash')
        setTimeout(() => el.classList.remove('field-flash'), 1600)
      } else if (++tries < SCROLL_TRIES) {
        setTimeout(scrollToField, 100)
      }
    }
    setTimeout(scrollToField, 200)
  }

  function activate(index = activeIndex.value) {
    const item = items.value[index]
    if (!item) return
    if (item.type === 'page') openPage(item.page)
    else openHit(item.hit, item.instance || undefined)
  }

  return {
    open, query, loading, settings, total, error,
    instanceNames, targetInstance, multiInstance, pageHits, hitGroups, activeIndex,
    toggle, close, reset, schedule, run, openPage, openHit, moveActive, activate,
  }
})
