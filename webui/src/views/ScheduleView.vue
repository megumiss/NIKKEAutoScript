<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import AppSelect from '../components/AppSelect.vue'
import { api } from '../api/client'
import { t } from '../i18n'
import { formatTime } from '../utils'
import { useToastStore } from '../stores/toast'
import { useWorkspaceStore } from '../stores/workspace'

type Cadence = 'daily' | 'weekly' | 'monthly'
interface ScheduleTask {
  command: string
  name_i18n: string
  cadence: Cadence
  cadence_locked: boolean
  next_run: string
  daily_times?: string // 逗号分隔的多个时间点
  weekly_days?: string // 逗号分隔的星期（1=周一 … 7=周日）
  weekly_time?: string
  monthly_day?: string // 每月第几天（1-28）
  monthly_time?: string
}
// 草稿全量快照三套周期字段 + 周期本身；编辑只作用于当前（草稿）周期的字段
interface Draft {
  cadence?: Cadence
  daily_times?: string[]
  weekly_days?: number[]
  weekly_time?: string
  monthly_day?: number
  monthly_time?: string
}

const workspace = useWorkspaceStore()
const { selectedName } = storeToRefs(workspace)
const toast = useToastStore()

const tasks = ref<ScheduleTask[]>([])
const loading = ref(false)
const saving = ref(false)
const drafts = ref<Record<string, Draft>>({})
const rowErrors = ref<Record<string, string>>({})
const cadenceTab = ref<'all' | Cadence>('all')
const keyword = ref('')
const selected = ref<Set<string>>(new Set())

const WEEKDAYS = [
  { value: 1, key: '周一' }, { value: 2, key: '周二' }, { value: 3, key: '周三' },
  { value: 4, key: '周四' }, { value: 5, key: '周五' }, { value: 6, key: '周六' }, { value: 7, key: '周日' },
]
const tabs = computed(() => [
  { value: 'all', label: t('全部') }, { value: 'daily', label: t('每日') },
  { value: 'weekly', label: t('每周') }, { value: 'monthly', label: t('每月') },
])
const cadenceOptions = computed(() => [
  { value: 'daily', label: t('每日') }, { value: 'weekly', label: t('每周') }, { value: 'monthly', label: t('每月') },
])

// 时间点/星期在解析时统一排序，保证行内展示与 draft 数组下标一致
function parseTimes(value?: string) { return String(value || '').split(',').map(item => item.trim()).filter(Boolean).sort() }
function parseDays(value?: string) { return parseTimes(value).map(Number).filter(day => day >= 1 && day <= 7).sort((a, b) => a - b) }

function draftOf(task: ScheduleTask): Draft {
  if (!drafts.value[task.command]) {
    drafts.value[task.command] = {
      cadence: task.cadence,
      daily_times: parseTimes(task.daily_times),
      weekly_days: parseDays(task.weekly_days),
      weekly_time: task.weekly_time ?? '',
      monthly_day: Number(task.monthly_day) || 1,
      monthly_time: task.monthly_time ?? '',
    }
  }
  return drafts.value[task.command]
}

// 徽章/筛选/编辑器都按草稿周期优先，切换周期后立即归入新分组
const effectiveCadence = (task: ScheduleTask): Cadence => drafts.value[task.command]?.cadence ?? task.cadence
const dailyTimes = (task: ScheduleTask) => drafts.value[task.command]?.daily_times ?? parseTimes(task.daily_times)
const weeklyDays = (task: ScheduleTask) => drafts.value[task.command]?.weekly_days ?? parseDays(task.weekly_days)
const weeklyTime = (task: ScheduleTask) => drafts.value[task.command]?.weekly_time ?? task.weekly_time ?? ''
const monthlyDay = (task: ScheduleTask) => drafts.value[task.command]?.monthly_day ?? (Number(task.monthly_day) || 1)
const monthlyTime = (task: ScheduleTask) => drafts.value[task.command]?.monthly_time ?? task.monthly_time ?? ''

function isDirty(task: ScheduleTask) {
  const draft = drafts.value[task.command]
  if (!draft) return false
  const cadence = draft.cadence ?? task.cadence
  if (cadence !== task.cadence) return true
  if (cadence === 'daily') return (draft.daily_times || []).join(',') !== parseTimes(task.daily_times).join(',')
  if (cadence === 'weekly') {
    return (draft.weekly_days || []).join(',') !== parseDays(task.weekly_days).join(',') || (draft.weekly_time ?? '') !== (task.weekly_time ?? '')
  }
  return Number(draft.monthly_day) !== (Number(task.monthly_day) || 1) || (draft.monthly_time ?? '') !== (task.monthly_time ?? '')
}
const dirtyCount = computed(() => tasks.value.filter(task => isDirty(task)).length)

function setCadence(task: ScheduleTask, cadence: Cadence) { draftOf(task).cadence = cadence }
function addTime(task: ScheduleTask, event: Event) {
  const input = event.target as HTMLInputElement
  const value = input.value
  input.value = ''
  if (!value) return
  const draft = draftOf(task)
  if (!draft.daily_times!.includes(value)) draft.daily_times = [...draft.daily_times!, value].sort()
}
function removeTime(task: ScheduleTask, index: number) {
  const draft = draftOf(task)
  if (draft.daily_times!.length <= 1) { toast.notify(t('至少保留一个时间点'), 'error'); return }
  draft.daily_times = draft.daily_times!.filter((_, i) => i !== index)
}
function toggleDay(task: ScheduleTask, day: number) {
  const draft = draftOf(task)
  const days = draft.weekly_days!
  if (days.includes(day)) {
    if (days.length <= 1) { toast.notify(t('至少保留一天'), 'error'); return }
    draft.weekly_days = days.filter(item => item !== day)
  } else {
    draft.weekly_days = [...days, day].sort((a, b) => a - b)
  }
}
function setWeeklyTime(task: ScheduleTask, event: Event) { draftOf(task).weekly_time = (event.target as HTMLInputElement).value }
function setMonthlyTime(task: ScheduleTask, event: Event) { draftOf(task).monthly_time = (event.target as HTMLInputElement).value }
function setMonthlyDay(task: ScheduleTask, event: Event) {
  const value = parseInt((event.target as HTMLInputElement).value, 10)
  // 非法值保留原样交给后端 422 校验，行标红提示
  draftOf(task).monthly_day = Number.isNaN(value) ? 0 : value
}

// next_run 年份 < 2000 是"尽快执行"的哨兵值
function nextRunText(task: ScheduleTask) {
  const value = String(task.next_run || '')
  const year = Number(value.slice(0, 4))
  if (!year || year < 2000) return t('尽快执行')
  return formatTime(value)
}
const cadenceText = (cadence: Cadence) => cadence === 'daily' ? t('每日') : cadence === 'weekly' ? t('每周') : t('每月')

const filteredTasks = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  return tasks.value.filter(task =>
    (cadenceTab.value === 'all' || effectiveCadence(task) === cadenceTab.value)
    && (!q || task.name_i18n.toLowerCase().includes(q) || task.command.toLowerCase().includes(q)))
})

const allFilteredSelected = computed(() => filteredTasks.value.length > 0 && filteredTasks.value.every(task => selected.value.has(task.command)))
function toggleSelectAll() {
  if (allFilteredSelected.value) filteredTasks.value.forEach(task => selected.value.delete(task.command))
  else filteredTasks.value.forEach(task => selected.value.add(task.command))
}
function toggleSelect(command: string) {
  if (selected.value.has(command)) selected.value.delete(command)
  else selected.value.add(command)
}

// 批量设置弹窗：只写入各行 draft（按各行当前草稿周期），不直接发请求
const batchOpen = ref(false)
const batchTimes = ref<string[]>([])
const batchDays = ref<number[]>([])
const selectionHasWeekly = computed(() => [...selected.value].some(command => {
  const task = tasks.value.find(item => item.command === command)
  return task ? effectiveCadence(task) === 'weekly' : false
}))
function openBatch() {
  batchTimes.value = ['04:00']
  batchDays.value = []
  batchOpen.value = true
}
function batchAddTime(event: Event) {
  const input = event.target as HTMLInputElement
  const value = input.value
  input.value = ''
  if (!value) return
  if (!batchTimes.value.includes(value)) batchTimes.value = [...batchTimes.value, value].sort()
}
function batchRemoveTime(index: number) {
  if (batchTimes.value.length <= 1) { toast.notify(t('至少保留一个时间点'), 'error'); return }
  batchTimes.value = batchTimes.value.filter((_, i) => i !== index)
}
function batchToggleDay(day: number) {
  if (batchDays.value.includes(day)) batchDays.value = batchDays.value.filter(item => item !== day)
  else batchDays.value = [...batchDays.value, day].sort((a, b) => a - b)
}
function applyBatch() {
  const first = batchTimes.value[0]
  if (!first) return
  for (const command of selected.value) {
    const task = tasks.value.find(item => item.command === command)
    if (!task) continue
    const draft = draftOf(task)
    const cadence = draft.cadence ?? task.cadence
    if (cadence === 'daily') draft.daily_times = [...batchTimes.value]
    else if (cadence === 'weekly') {
      draft.weekly_time = first
      if (batchDays.value.length) draft.weekly_days = [...batchDays.value]
    } else draft.monthly_time = first
  }
  batchOpen.value = false
}

function reset() {
  drafts.value = {}
  rowErrors.value = {}
}

async function save() {
  const changes = tasks.value.filter(task => isDirty(task)).map(task => {
    const draft = drafts.value[task.command]!
    const cadence = draft.cadence ?? task.cadence
    const change: Record<string, string> = { command: task.command }
    // cadence 不传表示不修改周期
    if (cadence !== task.cadence) change.cadence = cadence
    if (cadence === 'daily') change.daily_times = (draft.daily_times || []).join(', ')
    else if (cadence === 'weekly') { change.weekly_days = (draft.weekly_days || []).join(', '); change.weekly_time = draft.weekly_time ?? '' }
    else { change.monthly_day = String(draft.monthly_day ?? ''); change.monthly_time = draft.monthly_time ?? '' }
    return change
  })
  if (!changes.length || saving.value) return
  saving.value = true
  try {
    await api.post(`/api/${selectedName.value}/schedule/save`, { changes })
    toast.notify(t('保存成功'))
    drafts.value = {}
    rowErrors.value = {}
    await load()
  } catch (exception: any) {
    if (exception.errors) {
      rowErrors.value = exception.errors
      toast.notify(`${t('保存失败')}: ${exception.message}`, 'error', 6000)
    } else {
      toast.error = exception.message
    }
  } finally {
    saving.value = false
  }
}

async function load() {
  if (!selectedName.value) return
  loading.value = true
  try {
    const result = await api.get(`/api/${selectedName.value}/schedule`)
    tasks.value = result.tasks || []
    // 已消失的任务从选中集中剔除
    const commands = new Set(tasks.value.map((task: ScheduleTask) => task.command))
    selected.value = new Set([...selected.value].filter(command => commands.has(command)))
  } catch (exception: any) {
    toast.error = exception.message
  } finally {
    loading.value = false
  }
}
onMounted(load)
watch(selectedName, () => {
  drafts.value = {}
  rowErrors.value = {}
  selected.value = new Set()
  load()
})
</script>

<template>
  <section class="view sched-view">
    <article class="card sched-card">
      <div class="sched-toolbar">
        <div class="sched-tabs">
          <button v-for="tab in tabs" :key="tab.value" type="button" class="sched-tab" :class="{ active: cadenceTab === tab.value }" @click="cadenceTab = tab.value as any">{{ tab.label }}</button>
        </div>
        <label class="sched-search">🔍 <input v-model="keyword" :placeholder="t('搜索任务')"><button v-if="keyword" type="button" class="sched-search-clear" @click.prevent="keyword = ''">✕</button></label>
      </div>
      <div class="sched-select-bar">
        <label class="sched-check"><input type="checkbox" :checked="allFilteredSelected" @change="toggleSelectAll"> {{ t('全选') }}</label>
        <span class="sched-selected-count">{{ selected.size }} {{ t('项已选') }}</span>
        <button type="button" class="btn sm primary" :disabled="!selected.size" @click="openBatch">{{ t('批量设置时间') }}</button>
      </div>
      <div class="sched-list">
        <div v-if="loading && !tasks.length" class="sched-empty">{{ t('加载中…') }}</div>
        <div v-else-if="!filteredTasks.length" class="sched-empty">{{ t('没有匹配的任务') }}</div>
        <div v-for="task in filteredTasks" :key="task.command" class="sched-row" :class="{ dirty: isDirty(task), invalid: rowErrors[task.command] }">
          <input type="checkbox" class="sched-row-check" :checked="selected.has(task.command)" @change="toggleSelect(task.command)">
          <div class="sched-name">
            <b>{{ task.name_i18n }}</b>
            <span v-if="rowErrors[task.command]" class="sched-row-error">{{ rowErrors[task.command] }}</span>
          </div>
          <span :title="task.cadence_locked ? t('该任务不支持修改周期') : ''">
            <AppSelect class="sched-cadence" :model-value="effectiveCadence(task)" :options="cadenceOptions" :disabled="task.cadence_locked" @change="(value: Cadence) => setCadence(task, value)" />
          </span>
          <span class="sched-badge" :class="effectiveCadence(task)">{{ cadenceText(effectiveCadence(task)) }}</span>
          <div class="sched-editor">
            <template v-if="effectiveCadence(task) === 'daily'">
              <span v-for="(time, index) in dailyTimes(task)" :key="time" class="time-chip">🕒{{ time }}<button type="button" class="chip-x" @click="removeTime(task, index)">✕</button></span>
              <input type="time" class="sched-input sched-time-add" :title="t('添加时间')" @change="addTime(task, $event)">
            </template>
            <template v-else-if="effectiveCadence(task) === 'weekly'">
              <span class="sched-days">
                <button v-for="day in WEEKDAYS" :key="day.value" type="button" class="day-toggle" :class="{ on: weeklyDays(task).includes(day.value) }" @click="toggleDay(task, day.value)">{{ t(day.key) }}</button>
              </span>
              <input type="time" class="sched-input" :value="weeklyTime(task)" @change="setWeeklyTime(task, $event)">
            </template>
            <template v-else>
              <span class="sched-monthly">{{ t('每月第') }} <input type="number" class="sched-input sched-day-input" min="1" max="28" :value="monthlyDay(task)" @change="setMonthlyDay(task, $event)"> {{ t('日') }}</span>
              <input type="time" class="sched-input" :value="monthlyTime(task)" @change="setMonthlyTime(task, $event)">
            </template>
          </div>
          <span class="sched-next">{{ t('下次') }}: {{ nextRunText(task) }}</span>
        </div>
      </div>
    </article>
    <div v-if="dirtyCount" class="sched-footer">
      <span class="sched-dirty-dot">●</span> {{ t('有未保存的修改') }} ({{ dirtyCount }})
      <button type="button" class="btn sm" @click="reset">{{ t('重置') }}</button>
      <button type="button" class="btn sm primary" :disabled="saving" @click="save">{{ t('保存') }}</button>
    </div>
    <div v-if="batchOpen" class="modal-mask" @click.self="batchOpen = false">
      <div class="modal-card">
        <h3>{{ t('批量设置时间') }}</h3>
        <div class="sched-batch-times">
          <span v-for="(time, index) in batchTimes" :key="time" class="time-chip">🕒{{ time }}<button type="button" class="chip-x" @click="batchRemoveTime(index)">✕</button></span>
          <input type="time" class="sched-input sched-time-add" :title="t('添加时间')" @change="batchAddTime">
        </div>
        <div v-if="selectionHasWeekly" class="sched-batch-days">
          <button v-for="day in WEEKDAYS" :key="day.value" type="button" class="day-toggle" :class="{ on: batchDays.includes(day.value) }" @click="batchToggleDay(day.value)">{{ t(day.key) }}</button>
          <span class="sched-batch-hint">{{ t('仅作用于每周任务') }}</span>
        </div>
        <p class="modal-text">{{ t('每日任务将设置为全部时间点；每周/每月任务使用第一个时间点') }}</p>
        <div class="modal-actions">
          <button type="button" class="btn" @click="batchOpen = false">{{ t('取消') }}</button>
          <button type="button" class="btn primary" :disabled="!batchTimes.length" @click="applyBatch">{{ t('确定') }}</button>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.sched-card { padding: 16px 18px; }
.sched-toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
.sched-tabs { display: flex; gap: 6px; }
.sched-tab { padding: 6px 14px; border: 1px solid var(--border-light); border-radius: 9px; color: var(--text-2); background: var(--card-3); font-size: 13px; }
.sched-tab:hover { border-color: var(--accent); color: var(--accent); }
.sched-tab.active { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); font-weight: 700; }
.sched-search { display: flex; gap: 7px; align-items: center; min-width: 220px; padding: 7px 11px; border: 1px solid var(--border); border-radius: 9px; color: var(--text-3); background: var(--card-2); }
.sched-search input { width: 100%; border: 0; outline: 0; color: var(--text); background: transparent; font-size: 13px; }
.sched-search-clear { border: 0; color: var(--text-3); background: transparent; font-size: 12px; }
.sched-search-clear:hover { color: var(--red); }
.sched-select-bar { display: flex; gap: 14px; align-items: center; margin-top: 12px; padding: 8px 2px; font-size: 13px; color: var(--text-2); }
.sched-check { display: flex; gap: 6px; align-items: center; cursor: pointer; }
.sched-selected-count { color: var(--text-3); }
.sched-select-bar .btn { margin-left: auto; }
.sched-list { margin-top: 6px; border-top: 1px solid var(--border); }
.sched-empty { padding: 26px 0; color: var(--text-3); font-size: 13px; text-align: center; }
.sched-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; padding: 10px 8px; border-bottom: 1px solid var(--border); }
.sched-row.dirty { background: var(--accent-soft); }
.sched-row.invalid { box-shadow: inset 3px 0 0 var(--red); }
.sched-name { display: flex; flex-direction: column; min-width: 130px; }
.sched-row-error { color: var(--red); font-size: 11.5px; }
.sched-cadence { width: 96px; }
.sched-badge { flex: none; padding: 2px 9px; border-radius: 8px; font-size: 11.5px; background: var(--card-3); color: var(--text-2); }
.sched-badge.daily { color: var(--accent); background: var(--accent-soft); }
.sched-badge.weekly { color: var(--green); background: var(--green-soft); }
.sched-badge.monthly { color: var(--orange, #e2a35a); background: var(--card-3); }
.sched-editor { display: flex; flex: 1; flex-wrap: wrap; gap: 6px; align-items: center; }
.time-chip { display: inline-flex; gap: 5px; align-items: center; padding: 3px 6px 3px 9px; border: 1px solid var(--border-light); border-radius: 8px; background: var(--card-3); font-size: 12.5px; }
.chip-x { border: 0; padding: 0 2px; color: var(--text-3); background: transparent; font-size: 11px; }
.chip-x:hover { color: var(--red); }
.sched-input { padding: 4px 7px; border: 1px solid var(--border-light); border-radius: 8px; color: var(--text); background: var(--card-2); font-size: 12.5px; }
.sched-time-add { max-width: 96px; }
.sched-days { display: inline-flex; gap: 4px; }
.day-toggle { min-width: 34px; padding: 4px 6px; border: 1px solid var(--border-light); border-radius: 7px; color: var(--text-2); background: var(--card-3); font-size: 12px; }
.day-toggle:hover { border-color: var(--accent); color: var(--accent); }
.day-toggle.on { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); font-weight: 700; }
.sched-monthly { display: inline-flex; gap: 4px; align-items: center; color: var(--text-2); font-size: 12.5px; }
.sched-day-input { width: 58px; }
.sched-next { flex: none; color: var(--text-3); font-size: 12px; }
.sched-footer { position: sticky; z-index: 5; bottom: 0; display: flex; gap: 12px; align-items: center; justify-content: center; margin-top: 14px; padding: 12px; border: 1px solid var(--border-light); border-radius: 12px; background: var(--card); box-shadow: var(--shadow-hover); font-size: 13px; }
.sched-dirty-dot { color: var(--orange, #e2a35a); }
.sched-batch-times { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 14px; }
.sched-batch-days { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; margin-bottom: 12px; }
.sched-batch-hint { margin-left: 8px; color: var(--text-3); font-size: 11.5px; }
</style>
