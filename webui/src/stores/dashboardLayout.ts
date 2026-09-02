import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'

export interface DashboardLayoutItem {
  i: string
  x: number
  y: number
  w: number
  h: number
  minW?: number
  minH?: number
}

const STORAGE_KEY = 'nkas-dashboard-layout-v3'

function clone(items: DashboardLayoutItem[]) { return items.map(item => ({ ...item })) }

function readStored(): DashboardLayoutItem[] | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '')
    if (!Array.isArray(parsed)) return null
    return parsed.filter(item => item && typeof item.i === 'string' && Number.isFinite(item.x) && Number.isFinite(item.y) && Number.isFinite(item.w) && Number.isFinite(item.h))
  } catch { return null }
}

function defaultLayout(names: string[]) {
  const instanceHeight = Math.max(9, 4 + Math.ceil(names.length / 2) * 5)
  const summaryStart = 4 + instanceHeight
  const result: DashboardLayoutItem[] = [
    { i: 'stats', x: 0, y: 0, w: 12, h: 4, minW: 6, minH: 3 },
    { i: 'instances', x: 0, y: 4, w: 12, h: instanceHeight, minW: 6, minH: 7 },
  ]
  names.forEach((name, index) => {
    const row = summaryStart + Math.floor(index / 2) * 5
    result.push({ i: `instance:${name}`, x: index % 2 ? 6 : 0, y: row, w: 6, h: 5, minW: 4, minH: 4 })
  })
  result.push({ i: 'calendar', x: 0, y: summaryStart + Math.ceil(names.length / 2) * 5, w: 12, h: 40, minW: 8, minH: 12 })
  return result
}

export const useDashboardLayoutStore = defineStore('dashboardLayout', () => {
  const editing = ref(false)
  const layout = ref<DashboardLayoutItem[]>(readStored() || [])
  const defaultItems = ref<DashboardLayoutItem[]>([])
  const narrow = ref(false)

  const visibleLayout = computed(() => narrow.value
    ? layout.value.map((item, index) => ({ ...item, x: 0, y: index, w: 1, h: Math.max(1, item.h) }))
    : layout.value)

  function reconcile(names: string[]) {
    const defaults = defaultLayout(names)
    defaultItems.value = defaults
    const valid = new Set(defaults.map(item => item.i))
    const current = layout.value.filter(item => valid.has(item.i))
    const currentIds = new Set(current.map(item => item.i))
    defaults.forEach(item => { if (!currentIds.has(item.i)) current.push({ ...item }) })
    layout.value = current
    saveLayout()
  }

  function saveLayout() {
    if (narrow.value) return
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(layout.value)) } catch { /* storage is optional */ }
  }

  function resetLayout() {
    layout.value = clone(defaultItems.value)
    saveLayout()
  }

  function toggleEdit() { if (!narrow.value) editing.value = !editing.value }
  function setNarrow(value: boolean) { narrow.value = value; if (value) editing.value = false }

  watch(layout, saveLayout, { deep: true })
  return { editing, layout, visibleLayout, narrow, reconcile, saveLayout, resetLayout, toggleEdit, setNarrow }
})
