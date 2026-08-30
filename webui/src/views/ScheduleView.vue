<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import AppIcon from '../components/AppIcon.vue'
import AppSelect from '../components/AppSelect.vue'
import TimePicker from '../components/TimePicker.vue'
import { api } from '../api/client'
import { t } from '../i18n'
import { formatTime } from '../utils'
import { useToastStore } from '../stores/toast'
import { useWorkspaceStore } from '../stores/workspace'

type Cadence = 'daily' | 'weekly' | 'monthly'
interface ScheduleTask {
  command: string
  name_i18n: string
  enabled: boolean
  locked: boolean // 系统任务：整行置灰只读
  enable_locked: boolean // Enable 被强制锁定，不允许开关
  cadence: Cadence
  cadence_locked: boolean
  next_run: string
  daily_times?: string // 逗号分隔的多个时间点
  weekly_days?: string // 逗号分隔的星期（1=周一 … 7=周日）
  weekly_time?: string
  monthly_day?: string // 每月第几天（1-28）
  monthly_time?: string
}
// 草稿全量快照三套周期字段 + 周期本身 + 启用状态；编辑只作用于当前（草稿）周期的字段
interface Draft {
  enabled?: boolean
  cadence?: Cadence
  daily_times?: string[]
  weekly_days?: number[]
  weekly_time?: string
  monthly_day?: number
  monthly_time?: string
}

const workspace = useWorkspaceStore()
const { selectedName, schema } = storeToRefs(workspace)
const toast = useToastStore()

const tasks = ref<ScheduleTask[]>([])
const loading = ref(false)
const saving = ref(false)
const drafts = ref<Record<string, Draft>>({})
const rowErrors = ref<Record<string, string>>({})
const cadenceTab = ref<'all' | Cadence>('all')
const keyword = ref('')
const selected = ref<Set<string>>(new Set())
// 全选只作用于当前筛选结果，切页签时清空选中，避免把其他周期的隐藏任务带进批量操作
watch(cadenceTab, () => { selected.value = new Set() })

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
      enabled: task.enabled,
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
  if ((draft.enabled ?? task.enabled) !== task.enabled) return true
  const cadence = draft.cadence ?? task.cadence
  if (cadence !== task.cadence) return true
  if (cadence === 'daily') return (draft.daily_times || []).join(',') !== parseTimes(task.daily_times).join(',')
  if (cadence === 'weekly') {
    return (draft.weekly_days || []).join(',') !== parseDays(task.weekly_days).join(',') || (draft.weekly_time ?? '') !== (task.weekly_time ?? '')
  }
  return Number(draft.monthly_day) !== (Number(task.monthly_day) || 1) || (draft.monthly_time ?? '') !== (task.monthly_time ?? '')
}
const dirtyCount = computed(() => tasks.value.filter(task => isDirty(task)).length)

function setEnabled(task: ScheduleTask, event: Event) { draftOf(task).enabled = (event.target as HTMLInputElement).checked }
function setCadence(task: ScheduleTask, cadence: Cadence) { draftOf(task).cadence = cadence }
function addTime(task: ScheduleTask, value: string) {
  if (!value) return
  const draft = draftOf(task)
  if (!draft.daily_times!.includes(value)) draft.daily_times = [...draft.daily_times!, value].sort()
}
// 直接修改已添加的时间：与现有时间重复时只移除原项（等效于合并去重）
function changeTime(task: ScheduleTask, index: number, value: string) {
  if (!value) return
  const draft = draftOf(task)
  const times = [...draft.daily_times!]
  if (times.includes(value)) times.splice(index, 1)
  else times[index] = value
  draft.daily_times = times.sort()
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
function setWeeklyTime(task: ScheduleTask, value: string) { draftOf(task).weekly_time = value }
function setMonthlyTime(task: ScheduleTask, value: string) { draftOf(task).monthly_time = value }
function setMonthlyDay(task: ScheduleTask, event: Event) {
  const value = parseInt((event.target as HTMLInputElement).value, 10)
  // 非法值保留原样交给后端 422 校验，行标红提示
  draftOf(task).monthly_day = Number.isNaN(value) ? 0 : value
}

// next_run 早于当前时间（含 1989 哨兵值）即为已到期：启用中的任务显示"尽快执行"，
// 未启用的不会被调度，显示"未启用"避免误导
function nextRunText(task: ScheduleTask) {
  const time = new Date(String(task.next_run || '').replace(' ', 'T')).getTime()
  if (!time) return '-'
  if (time < Date.now()) return task.enabled ? t('尽快执行') : t('未启用')
  return formatTime(task.next_run)
}

const filteredTasks = computed(() => {
  const q = keyword.value.trim().toLowerCase()
  return tasks.value.filter(task =>
    (cadenceTab.value === 'all' || effectiveCadence(task) === cadenceTab.value)
    && (!q || task.name_i18n.toLowerCase().includes(q) || task.command.toLowerCase().includes(q)))
})

// 布局切换：列表 / 按任务分组（分组来自菜单定义，与左侧任务栏一致）
type ViewMode = 'list' | 'group'
const viewMode = ref<ViewMode>(localStorage.getItem('nkas-schedule-view') === 'group' ? 'group' : 'list')
watch(viewMode, value => localStorage.setItem('nkas-schedule-view', value))

const displayGroups = computed(() => {
  if (viewMode.value === 'list') return [{ key: '', name: '', icon: '', tasks: filteredTasks.value }]
  const byCommand = new Map(filteredTasks.value.map(task => [task.command, task]))
  const assigned = new Set<string>()
  const groups: { key: string, name: string, icon: string, tasks: ScheduleTask[] }[] = []
  for (const menu of (schema.value.menus || []) as any[]) {
    const items = menu.tasks.map((item: any) => byCommand.get(item.key)).filter(Boolean) as ScheduleTask[]
    items.forEach(task => assigned.add(task.command))
    if (items.length) groups.push({ key: menu.key, name: menu.name, icon: menu.icon || '', tasks: items })
  }
  const rest = filteredTasks.value.filter(task => !assigned.has(task.command))
  if (rest.length) groups.push({ key: '__other', name: t('其他'), icon: 'box', tasks: rest })
  return groups
})

const allFilteredSelected = computed(() => {
  const selectable = filteredTasks.value.filter(task => !task.locked && !task.cadence_locked)
  return selectable.length > 0 && selectable.every(task => selected.value.has(task.command))
})
function toggleSelectAll() {
  const selectable = filteredTasks.value.filter(task => !task.locked && !task.cadence_locked)
  if (allFilteredSelected.value) selectable.forEach(task => selected.value.delete(task.command))
  else selectable.forEach(task => selected.value.add(task.command))
}
function toggleSelect(command: string) {
  const task = tasks.value.find(item => item.command === command)
  if (!task || task.locked || task.cadence_locked) return
  if (selected.value.has(command)) selected.value.delete(command)
  else selected.value.add(command)
}

// 批量设置弹窗：周期在弹窗里直接选，所选任务统一应用该周期+时间（只写草稿，不直接发请求）
const batchOpen = ref(false)
const batchCadence = ref<Cadence>('daily')
const batchTimes = ref<string[]>([])
const batchDays = ref<number[]>([])
// 空字符串表示不修改各任务原有的每月执行日
const batchMonthlyDay = ref('')
function openBatch() {
  // 弹窗周期跟随当前页签，「全部」页签默认每日
  batchCadence.value = cadenceTab.value === 'all' ? 'daily' : cadenceTab.value
  batchTimes.value = ['04:00']
  batchDays.value = []
  batchMonthlyDay.value = ''
  batchOpen.value = true
}
function batchAddTime(value: string) {
  if (!value) return
  if (!batchTimes.value.includes(value)) batchTimes.value = [...batchTimes.value, value].sort()
}
function batchChangeTime(index: number, value: string) {
  if (!value) return
  if (batchTimes.value.includes(value)) batchTimes.value = batchTimes.value.filter((_, i) => i !== index)
  else batchTimes.value = batchTimes.value.map((time, i) => i === index ? value : time).sort()
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
  for (const task of filteredTasks.value) {
    if (!selected.value.has(task.command)) continue
    const draft = draftOf(task)
    draft.cadence = batchCadence.value
    if (batchCadence.value === 'daily') draft.daily_times = [...batchTimes.value]
    else if (batchCadence.value === 'weekly') {
      draft.weekly_time = first
      if (batchDays.value.length) draft.weekly_days = [...batchDays.value]
    } else {
      draft.monthly_time = first
      const day = Number(batchMonthlyDay.value)
      if (Number.isInteger(day) && day >= 1 && day <= 28) draft.monthly_day = day
    }
  }
  batchOpen.value = false
}

function reset() {
  drafts.value = {}
  rowErrors.value = {}
}

// 还原默认：直接调后端重置（周期+时间回默认值，启用状态不动），不走草稿
const resetOpen = ref(false)
const resetting = ref(false)
async function resetDefaults() {
  if (resetting.value) return
  resetting.value = true
  try {
    await api.post(`/api/${selectedName.value}/schedule/reset`)
    toast.notify(t('已还原为默认值'))
    resetOpen.value = false
    drafts.value = {}
    rowErrors.value = {}
    await load()
  } catch (exception: any) {
    toast.error = exception.message
  } finally {
    resetting.value = false
  }
}

async function save() {
  const changes = tasks.value.filter(task => isDirty(task)).map(task => {
    const draft = drafts.value[task.command]!
    const cadence = draft.cadence ?? task.cadence
    const change: Record<string, string | boolean> = { command: task.command }
    // enable/cadence 不传表示不修改
    const enabled = draft.enabled ?? task.enabled
    if (enabled !== task.enabled) change.enable = enabled
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
    // 调度保存/还原直接改写了配置，把最新 Enable/NextRun 同步进 workspace 缓存的
    // schema，否则打开任务设置页会看到加载时的旧值（schema 只在切换实例时重新拉取）
    for (const task of tasks.value) {
      for (const group of schema.value.tasks[task.command]?.groups || []) {
        if (group.key !== 'Scheduler') continue
        for (const field of group.fields) {
          if (field.arg === 'Enable') field.value = task.enabled
          else if (field.arg === 'NextRun') field.value = task.next_run
        }
      }
    }
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
        <label class="sched-search"><AppIcon name="search-normal" :size="14" /> <input v-model="keyword" :placeholder="t('搜索任务')"><button v-if="keyword" type="button" class="sched-search-clear" @click.prevent="keyword = ''"><AppIcon name="x" :size="12" /></button></label>
        <div class="sched-view-toggle">
          <button type="button" class="sched-tab" :class="{ active: viewMode === 'list' }" :title="t('列表')" @click="viewMode = 'list'"><AppIcon name="menu" :size="14" /></button>
          <button type="button" class="sched-tab" :class="{ active: viewMode === 'group' }" :title="t('分组')" @click="viewMode = 'group'"><AppIcon name="grid" :size="14" /></button>
        </div>
      </div>
      <div class="sched-select-bar">
        <label class="sched-check"><span class="cbox" :class="{ on: allFilteredSelected }"><input type="checkbox" hidden :checked="allFilteredSelected" @change="toggleSelectAll"></span> {{ t('全选') }}</label>
        <span class="sched-selected-count">{{ selected.size }} {{ t('项已选') }}</span>
        <p class="sched-hint"><AppIcon name="lightbulb" :size="13" /> {{ t('每日任务建议保持同一时间：到点后按优先级一次跑完。分散到不同时间会打乱执行顺序，可能导致漏领奖励；需要一天跑多次的任务再单独添加时间。') }}</p>
        <button type="button" class="btn sm danger" @click="resetOpen = true">{{ t('还原默认') }}</button>
        <button type="button" class="btn sm primary" :disabled="!selected.size" @click="openBatch">{{ t('批量设置时间') }}</button>
      </div>
      <div class="sched-list">
        <div v-if="loading && !tasks.length" class="sched-empty">{{ t('加载中…') }}</div>
        <div v-else-if="!filteredTasks.length" class="sched-empty">{{ t('没有匹配的任务') }}</div>
        <template v-else>
          <div class="sched-row sched-head">
            <span></span>
            <span>{{ t('任务') }}</span>
            <span>{{ t('周期') }}</span>
            <span>{{ t('执行时间') }}</span>
            <span>{{ t('下次运行') }}</span>
            <span>{{ t('启用') }}</span>
          </div>
          <template v-for="group in displayGroups" :key="group.key || '__all'">
            <div v-if="group.key" class="sched-group-head"><span class="sicon"><AppIcon :name="group.icon || 'box'" :size="16" /></span>{{ group.name }}</div>
            <div v-for="task in group.tasks" :key="task.command" class="sched-row" :class="{ dirty: isDirty(task), invalid: rowErrors[task.command], locked: task.locked }" :title="task.locked ? t('该任务由系统调度，仅展示') : ''">
              <label class="cbox" :class="{ on: selected.has(task.command), disabled: task.locked || task.cadence_locked }"><input type="checkbox" hidden :checked="selected.has(task.command)" :disabled="task.locked || task.cadence_locked" @change="toggleSelect(task.command)"></label>
              <div class="sched-name">
                <b class="sched-name-link" :title="t('打开任务设置')" @click="workspace.openQueueItem(task)">{{ task.name_i18n }}</b>
                <span v-if="rowErrors[task.command]" class="sched-row-error">{{ rowErrors[task.command] }}</span>
              </div>
            <span :title="task.cadence_locked ? t('该任务不支持修改周期') : ''">
              <AppSelect class="sched-cadence" :model-value="effectiveCadence(task)" :options="cadenceOptions" :disabled="task.cadence_locked" @change="(value: Cadence) => setCadence(task, value)" />
            </span>
            <div class="sched-editor">
              <template v-if="effectiveCadence(task) === 'daily'">
                <span v-for="(time, index) in dailyTimes(task)" :key="time" class="time-chip">
                  <TimePicker :model-value="time" :disabled="task.locked" @change="(value: string) => changeTime(task, index, value)" />
                  <button type="button" class="chip-x" :disabled="task.locked" @click="removeTime(task, index)"><AppIcon name="x" :size="10" /></button>
                </span>
                <TimePicker class="sched-time-add" :title="t('添加时间')" :disabled="task.locked" @change="(value: string) => addTime(task, value)" />
              </template>
              <template v-else-if="effectiveCadence(task) === 'weekly'">
                <span class="sched-days">
                  <button v-for="day in WEEKDAYS" :key="day.value" type="button" class="day-toggle" :class="{ on: weeklyDays(task).includes(day.value) }" :disabled="task.locked" @click="toggleDay(task, day.value)">{{ t(day.key) }}</button>
                </span>
                <TimePicker :model-value="weeklyTime(task)" :disabled="task.locked" @change="(value: string) => setWeeklyTime(task, value)" />
              </template>
              <template v-else>
                <span class="sched-monthly">{{ t('每月第') }} <input type="number" class="sched-input sched-day-input" min="1" max="28" :value="monthlyDay(task)" :disabled="task.locked" @change="setMonthlyDay(task, $event)"> {{ t('日') }}</span>
                <TimePicker :model-value="monthlyTime(task)" :disabled="task.locked" @change="(value: string) => setMonthlyTime(task, value)" />
              </template>
            </div>
            <span class="sched-next">{{ nextRunText(task) }}</span>
            <label class="switch sm" :title="task.enable_locked ? t('该任务的启用状态由系统锁定') : t('启用该任务')">
              <input type="checkbox" :checked="drafts[task.command]?.enabled ?? task.enabled" :disabled="task.enable_locked" @change="setEnabled(task, $event)">
              <span class="slider"></span>
            </label>
          </div>
          </template>
        </template>
      </div>
    </article>
    <div v-if="dirtyCount" class="sched-footer">
      <span class="sched-dirty-dot">●</span> {{ t('有未保存的修改') }} ({{ dirtyCount }})
      <button type="button" class="btn sm" @click="reset">{{ t('重置') }}</button>
      <button type="button" class="btn sm primary" :disabled="saving" @click="save">{{ t('保存') }}</button>
    </div>
    <div v-if="resetOpen" class="modal-mask" @click.self="resetOpen = false">
      <div class="modal-card">
        <h3>{{ t('还原默认') }}</h3>
        <p class="modal-text">{{ t('将所有任务的周期与执行时间还原为默认值？启用状态不受影响。') }}</p>
        <div class="modal-actions">
          <button type="button" class="btn" @click="resetOpen = false">{{ t('取消') }}</button>
          <button type="button" class="btn danger" :disabled="resetting" @click="resetDefaults">{{ t('确定') }}</button>
        </div>
      </div>
    </div>
    <div v-if="batchOpen" class="modal-mask" @click.self="batchOpen = false">
      <div class="modal-card">
        <h3>{{ t('批量设置时间') }}</h3>
        <div class="sched-batch-row">
          <span class="sched-batch-label">{{ t('周期') }}</span>
          <AppSelect class="sched-batch-cadence" v-model="batchCadence" :options="cadenceOptions" />
        </div>
        <div v-if="batchCadence === 'weekly'" class="sched-batch-row">
          <span class="sched-batch-label">{{ t('执行日') }}</span>
          <div class="sched-batch-days">
            <button v-for="day in WEEKDAYS" :key="day.value" type="button" class="day-toggle" :class="{ on: batchDays.includes(day.value) }" @click="batchToggleDay(day.value)">{{ t(day.key) }}</button>
          </div>
        </div>
        <div v-else-if="batchCadence === 'monthly'" class="sched-batch-row">
          <span class="sched-batch-label">{{ t('执行日') }}</span>
          <div class="sched-batch-days">
            <span class="sched-monthly">{{ t('每月第') }} <input type="number" class="sched-input sched-day-input" min="1" max="28" v-model="batchMonthlyDay"> {{ t('日') }}</span>
          </div>
        </div>
        <div class="sched-batch-row">
          <span class="sched-batch-label">{{ t('执行时间') }}</span>
          <div class="sched-batch-times">
            <span v-for="(time, index) in batchTimes" :key="time" class="time-chip">
              <TimePicker :model-value="time" @change="(value: string) => batchChangeTime(index, value)" />
              <button type="button" class="chip-x" @click="batchRemoveTime(index)"><AppIcon name="x" :size="10" /></button>
            </span>
            <TimePicker class="sched-time-add" :title="t('添加时间')" @change="batchAddTime" />
          </div>
        </div>
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
.sched-toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; }
.sched-tabs { display: flex; gap: 6px; }
.sched-tab { padding: 6px 14px; border: 1px solid var(--border-light); border-radius: 9px; color: var(--text-2); background: var(--card-3); font-size: 13px; }
.sched-tab:hover { border-color: var(--accent); color: var(--accent); }
.sched-tab.active { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); font-weight: 700; }
.sched-hint { flex: 1; min-width: 0; margin: 0; color: var(--text-3); font-size: 12px; line-height: 1.5; }
.sched-search { display: flex; gap: 7px; align-items: center; min-width: 220px; margin-left: auto; padding: 7px 11px; border: 1px solid var(--border); border-radius: 9px; color: var(--text-3); background: var(--card-2); }
.sched-search input { width: 100%; border: 0; outline: 0; color: var(--text); background: transparent; font-size: 13px; }
.sched-search-clear { border: 0; color: var(--text-3); background: transparent; font-size: 12px; }
.sched-search-clear:hover { color: var(--red); }
.sched-select-bar { display: flex; gap: 14px; align-items: center; margin-top: 12px; padding: 8px 2px; font-size: 13px; color: var(--text-2); }
.sched-check { display: flex; gap: 6px; align-items: center; cursor: pointer; }
.sched-selected-count { color: var(--text-3); }
.sched-list { margin-top: 6px; border-top: 1px solid var(--border); }
.sched-view-toggle { display: flex; gap: 6px; }
.sched-group-head { display: flex; gap: 7px; align-items: center; padding: 14px 8px 4px; color: var(--text-3); font-size: 13px; font-weight: 600; letter-spacing: .05em; }
.sched-empty { padding: 26px 0; color: var(--text-3); font-size: 13px; text-align: center; }
.sched-row { display: grid; grid-template-columns: 22px minmax(120px, 150px) 100px minmax(0, 1fr) 110px 40px; gap: 10px; align-items: center; padding: 10px 8px; border-bottom: 1px solid var(--border); }
.sched-row.dirty { background: var(--accent-soft); }
.sched-row.invalid { box-shadow: inset 3px 0 0 var(--red); }
.sched-row.locked { opacity: .55; }
.sched-head { padding: 8px; color: var(--text-3); font-size: 11.5px; font-weight: 600; }
.cbox { position: relative; display: inline-block; flex: none; width: 16px; height: 16px; border: 1px solid var(--border-light); border-radius: 5px; background: var(--card); cursor: pointer; transition: border-color .15s, background .15s; }
.cbox:hover { border-color: var(--accent); }
.cbox.on { border-color: transparent; background: var(--grad-accent); --check-mask: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M21.5303 5.46967C21.8232 5.76256 21.8232 6.23744 21.5303 6.53033L9.53033 18.5303C9.23744 18.8232 8.76256 18.8232 8.46967 18.5303L2.46967 12.5303C2.17678 12.2374 2.17678 11.7626 2.46967 11.4697C2.76256 11.1768 3.23744 11.1768 3.53033 11.4697L9 16.9393L20.4697 5.46967C20.7626 5.17678 21.2374 5.17678 21.5303 5.46967Z' fill='%23fff'/%3E%3C/svg%3E"); }
.cbox.on::after { position: absolute; inset: 0; content: ''; background: #fff; -webkit-mask: var(--check-mask) center / 10px 10px no-repeat; mask: var(--check-mask) center / 10px 10px no-repeat; }
.cbox.disabled { cursor: not-allowed; }
.sched-name { display: flex; flex-direction: column; }
.sched-name-link { cursor: pointer; transition: color .15s; }
.sched-name-link:hover { color: var(--accent); }
.sched-row-error { color: var(--red); font-size: 11.5px; }
.sched-cadence { width: 100px; }
.sched-cadence :deep(.app-select) { width: 100%; margin-top: 0; }
.sched-cadence :deep(.app-select-btn) { height: 30px; padding: 0 10px; border-color: var(--border-light); background: var(--card); font-size: 12.5px; font-weight: 600; }
.sched-cadence :deep(.app-select-btn:hover) { border-color: var(--accent); color: var(--accent); }
.sched-cadence :deep(.app-select.open .app-select-btn) { border-color: var(--accent); }
.sched-cadence :deep(.app-select-arrow) { color: var(--accent); }
.sched-editor { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.time-chip { display: inline-flex; gap: 2px; align-items: center; width: 104px; height: 30px; padding: 0 5px 0 3px; border: 1px solid var(--border); border-radius: 8px; background: var(--card-2); transition: border-color .15s; }
.time-chip:hover { border-color: var(--border-light); }
.time-chip :deep(.tp) { flex: 1; min-width: 0; }
.time-chip :deep(.tp-btn) { height: 28px; padding: 0 2px; border: 0; background: transparent; }
.time-chip :deep(.tp-btn:hover) { border-color: transparent; }
.time-chip :deep(.tp.open .tp-btn) { box-shadow: none; }
.time-chip :deep(.tp-arrow) { font-size: 11px; }
.chip-x { border: 0; padding: 0 2px; color: var(--text-3); background: transparent; font-size: 11px; }
.chip-x:hover { color: var(--red); }
.sched-input { height: 30px; padding: 0 8px; border: 1px solid var(--border); border-radius: 8px; outline: 0; color: var(--text); background: var(--card-2); font-size: 12.5px; transition: border-color .15s, box-shadow .15s; }
.sched-input:hover { border-color: var(--border-light); }
.sched-input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.sched-input:disabled { cursor: not-allowed; }
.sched-input::-webkit-calendar-picker-indicator { cursor: pointer; }
.sched-time-add { width: 104px; }
.sched-days { display: inline-flex; gap: 4px; }
.day-toggle { min-width: 34px; height: 26px; padding: 0 6px; border: 1px solid var(--border); border-radius: 7px; color: var(--text-2); background: var(--card-2); font-size: 12px; transition: border-color .15s, color .15s; }
.day-toggle:hover { border-color: var(--accent); color: var(--accent); }
.day-toggle.on { border-color: var(--accent); color: var(--accent); background: var(--accent-soft); font-weight: 700; }
.sched-monthly { display: inline-flex; gap: 4px; align-items: center; color: var(--text-2); font-size: 12.5px; }
.sched-day-input { width: 58px; }
.sched-next { flex: none; color: var(--text-3); font-size: 12px; }
.sched-footer { position: sticky; z-index: 5; bottom: 0; display: flex; gap: 12px; align-items: center; justify-content: center; margin-top: 14px; padding: 12px; border: 1px solid var(--border-light); border-radius: 12px; background: var(--card); box-shadow: var(--shadow-hover); font-size: 13px; }
.sched-dirty-dot { color: var(--orange, #e2a35a); }
.sched-batch-row { display: flex; gap: 12px; align-items: flex-start; margin-bottom: 12px; }
.sched-batch-label { flex: none; width: 56px; padding-top: 8px; color: var(--text-2); font-size: 13px; text-align: right; }
.sched-batch-times { display: flex; flex: 1; flex-wrap: wrap; gap: 6px; align-items: center; }
.sched-batch-cadence { width: 160px; }
.sched-batch-days { display: flex; flex: 1; flex-wrap: wrap; gap: 4px; align-items: center; padding-top: 4px; }
</style>
