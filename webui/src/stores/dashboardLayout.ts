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

const STORAGE_KEY = 'nkas-dashboard-layout-v11'
const HIDDEN_KEY = 'nkas-dashboard-hidden-v1'

function clone(items: DashboardLayoutItem[]) { return items.map(item => ({ ...item })) }

function collides(a: DashboardLayoutItem, b: DashboardLayoutItem) {
  return a.i !== b.i && a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
}

// 越界或互相重叠的存储布局直接作废，回退默认布局
function isSane(items: DashboardLayoutItem[]) {
  for (const item of items) {
    if (item.w < 1 || item.w > 12 || item.h < 1 || item.h > 100) return false
    if (item.x < 0 || item.x + item.w > 12 || item.y < 0 || item.y > 200) return false
  }
  return !items.some((item, index) => items.slice(index + 1).some(other => collides(item, other)))
}

function validShape(item: any) {
  return item && typeof item.i === 'string' && Number.isFinite(item.x) && Number.isFinite(item.y) && Number.isFinite(item.w) && Number.isFinite(item.h)
}

function readStored(): DashboardLayoutItem[] | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(STORAGE_KEY) || '')
    if (!Array.isArray(parsed) || !parsed.length) return null
    const items = parsed.filter(validShape)
    if (!items.length || !isSane(items)) return null
    return items
  } catch { return null }
}

function readHidden(): DashboardLayoutItem[] {
  try {
    const parsed = JSON.parse(localStorage.getItem(HIDDEN_KEY) || '')
    if (!Array.isArray(parsed)) return []
    return parsed.filter(validShape)
  } catch { return [] }
}

function defaultLayout(names: string[]) {
  // 高度为网格行数（rowHeight 17 + 纵向 margin 18）。行高取小是为了让分区高度贴近内容：
  // 行高太大时各分区「凑整余量」不一（实例区余 39px、概览区余 15px），区间距肉眼可见地不均匀
  // 实例卡片内容约 220px，加分区标题 35px 共 255px，需 8 行（262px）
  const instanceHeight = Math.max(8, Math.ceil(names.length / 3) * 8)
  // 实例区宽度按卡片数取整行 1/3、2/3、整行（卡片约 400px ≈ 4 列），少实例时外框不留大片空白
  const instanceCols = Math.min(Math.max(names.length, 1), 3)
  const summaryStart = 4 + instanceHeight
  const result: DashboardLayoutItem[] = [
    { i: 'stats', x: 0, y: 0, w: 12, h: 4, minW: 6, minH: 4 },
    { i: 'instances', x: 0, y: 4, w: instanceCols * 4, h: instanceHeight, minW: 4, minH: 8 },
  ]
  names.forEach((name, index) => {
    const row = summaryStart + Math.floor(index / 2) * 7
    result.push({ i: `instance:${name}`, x: index % 2 ? 6 : 0, y: row, w: 6, h: 7, minW: 4, minH: 6 })
  })
  result.push({ i: 'calendar', x: 0, y: summaryStart + Math.ceil(names.length / 2) * 7, w: 12, h: 24, minW: 6, minH: 12 })
  return result
}

export const useDashboardLayoutStore = defineStore('dashboardLayout', () => {
  const editing = ref(false)
  const layout = ref<DashboardLayoutItem[]>(readStored() || [])
  const defaultItems = ref<DashboardLayoutItem[]>([])
  const narrow = ref(false)
  // 隐藏的卡片单独存放（保留位置），不进入 layout，避免网格压缩后位置互相覆盖
  const hidden = ref<DashboardLayoutItem[]>(readHidden())

  const visibleLayout = computed(() => narrow.value
    // 窄屏单列：宽度收拢为 1 列，按顺序纵向堆叠；不写回持久化布局
    ? layout.value.map((item, index) => ({ i: item.i, x: 0, y: index, w: 1, h: Math.max(1, item.h) }))
    : layout.value)

  function reconcile(names: string[], loading = false) {
    const previousDefaults = new Map(defaultItems.value.map(item => [item.i, item]))
    const defaults = defaultLayout(names)
    defaultItems.value = defaults
    const defaultById = new Map(defaults.map(item => [item.i, item]))
    const valid = new Set(defaults.map(item => item.i))
    // 实例列表加载完成前保留已存的实例卡片：否则刷新后实例卡片被误删，
    // 列表就绪后又按默认位置重建，用户调整过的布局被还原
    let kept = layout.value.filter(item => valid.has(item.i) || (loading && item.i.startsWith('instance:')))
    hidden.value = hidden.value.filter(item => valid.has(item.i) || (loading && item.i.startsWith('instance:')))
    // 没有被用户动过的分区跟随新默认：实例数变化时实例区宽度/高度自适应
    kept = kept.map(item => {
      const prev = previousDefaults.get(item.i)
      const next = defaultById.get(item.i)
      if (prev && next && item.x === prev.x && item.y === prev.y && item.w === prev.w && item.h === prev.h) return { ...next }
      return item
    })
    // 运行期布局也可能被写坏（拖拽中断等），发现重叠直接整体回退默认
    if (kept.some((item, index) => kept.slice(index + 1).some(other => collides(item, other)))) kept = []
    const keptIds = new Set(kept.map(item => item.i))
    // 被用户隐藏的卡片不当作「缺失」重建
    const hiddenIds = new Set(hidden.value.map(item => item.i))
    const added = defaults.filter(item => !keptIds.has(item.i) && !hiddenIds.has(item.i)).map(item => ({ ...item }))
    // 新增卡片优先按默认位置落位（实例列表异步加载，不能假设一次到齐），
    // 被撞占位的既有卡片依次向下避让，保证「实例概览在实例区下方、日历最后」
    const placed = [...added]
    const rest = [...kept].sort((a, b) => a.y - b.y || a.x - b.x)
    for (const item of rest) {
      let hit: DashboardLayoutItem | undefined
      while ((hit = placed.find(other => collides(other, item)))) item.y = hit.y + hit.h
      placed.push(item)
    }
    layout.value = [...added, ...rest]
  }

  function saveLayout() {
    if (narrow.value) return
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(layout.value)) } catch { /* storage is optional */ }
  }

  function resetLayout() {
    layout.value = clone(defaultItems.value)
    hidden.value = []
    saveLayout()
  }

  function hide(id: string) {
    const item = layout.value.find(entry => entry.i === id)
    if (!item) return
    layout.value = layout.value.filter(entry => entry.i !== id)
    hidden.value = [...hidden.value, item]
  }

  function show(id: string) {
    const item = hidden.value.find(entry => entry.i === id)
    if (!item) return
    hidden.value = hidden.value.filter(entry => entry.i !== id)
    // 按记住的位置落位；与现有卡片重叠时现有卡片向下避让（与 reconcile 的新增逻辑一致）
    const placed = [item]
    const rest = layout.value.filter(entry => entry.i !== id).sort((a, b) => a.y - b.y || a.x - b.x)
    for (const other of rest) {
      let hit: DashboardLayoutItem | undefined
      while ((hit = placed.find(entry => collides(entry, other)))) other.y = hit.y + hit.h
      placed.push(other)
    }
    layout.value = [item, ...rest]
  }

  function saveHidden() {
    try { localStorage.setItem(HIDDEN_KEY, JSON.stringify(hidden.value)) } catch { /* storage is optional */ }
  }

  function toggleEdit() { if (!narrow.value) editing.value = !editing.value }
  function setNarrow(value: boolean) { narrow.value = value; if (value) editing.value = false }

  watch(layout, saveLayout, { deep: true })
  watch(hidden, saveHidden, { deep: true })
  return { editing, layout, visibleLayout, narrow, hidden, reconcile, saveLayout, resetLayout, hide, show, toggleEdit, setNarrow }
})
