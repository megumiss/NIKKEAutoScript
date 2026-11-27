<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import AppIcon from '../AppIcon.vue'
import { api } from '../../api/client'
import { t, uiLanguage } from '../../i18n'
import { useToastStore } from '../../stores/toast'

type DropStats = {
  today: string
  save_dir: string
  days: Record<string, number>
}
const props = defineProps<{ stats: DropStats; endpoint: string }>()
const emit = defineEmits<{ updated: [stats: DropStats] }>()
const toast = useToastStore()
const trigger = ref<HTMLButtonElement>()
const dialog = ref<HTMLDialogElement>()
const day = ref(props.stats.today)
const month = computed(() => day.value.slice(0, 7))
const drafts = ref<Record<string, string>>({})
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const notice = ref('')
const currentMonth = computed(() => props.stats.today.slice(0, 7))
/** 按服务端的自然月计算格子日期；全程使用 UTC 避免客户端时区或夏令时把日期移到前一天。 */
function monthDate(delta = 0, dayOfMonth = 1) {
  const date = new Date(`${month.value}-01T00:00:00Z`)
  date.setUTCMonth(date.getUTCMonth() + delta, dayOfMonth)
  return date
}
const monthTitle = computed(() => monthDate().toLocaleDateString(uiLanguage.value, {
  year: 'numeric', month: 'long', timeZone: 'UTC',
}))
const offset = computed(() => (monthDate().getUTCDay() + (uiLanguage.value === 'zh-CN' ? 6 : 0)) % 7)
const weekdays = computed(() => {
  const format = new Intl.DateTimeFormat(uiLanguage.value, { weekday: 'short', timeZone: 'UTC' })
  return Array.from({ length: 7 }, (_, i) => format.format(monthDate(0, i + 1 - offset.value)))
})
const days = computed(() => Array.from({ length: monthDate(1, 0).getUTCDate() }, (_, i) => {
  const date = `${month.value}-${String(i + 1).padStart(2, '0')}`
  return { date, number: i + 1, total: props.stats.days[date], future: date > props.stats.today }
}))
const recorded = computed(() => props.stats.days[day.value])
const pending = computed(() => Object.entries(drafts.value))
const invalidDay = computed(() => pending.value.find(([, value]) => !validCount(value))?.[0])
const canSave = computed(() => !saving.value && pending.value.length > 0 && !invalidDay.value)

/** 空值和超出安全整数范围的数量不能提交，显式填写的零是有效掉落数。 */
function validCount(value: string) {
  return /^\d+$/.test(value) && Number.isSafeInteger(Number(value))
}

/** 详情始终对应一个日期；选择和草稿独立，切换日期或月份不会丢失输入。 */
function selectDay(date: string) {
  day.value = date
  notice.value = ''
}

/** 打开前重新读取统计；原来停在本月时跟随服务端跨月，浏览历史月份时则保留该月。 */
async function openEditor() {
  if (loading.value) return
  loading.value = true
  const followToday = month.value === currentMonth.value
  try {
    const stats: DropStats = await api.get(props.endpoint)
    if (!dialog.value) return
    emit('updated', stats)
    drafts.value = {}
    error.value = ''
    selectDay(followToday || day.value > stats.today ? stats.today : day.value)
    await nextTick()
    dialog.value?.showModal()
  } catch (exception: unknown) {
    if (dialog.value) toast.error = exception instanceof Error ? exception.message : t('未能读取掉落统计，请刷新页面重试。')
  } finally {
    loading.value = false
  }
}

/** 切换可浏览的月份；本月默认选中今天，历史月份默认选中首日。 */
function showMonth(delta = 0) {
  const target = delta ? monthDate(delta).toISOString().slice(0, 7) : currentMonth.value
  if (saving.value || target > currentMonth.value) return
  selectDay(target === currentMonth.value ? props.stats.today : `${target}-01`)
}

/** 点击日期或用键盘进入格子即更新详情；重新聚焦同一天不覆盖未保存的输入。 */
function focusDay(date: string, event: FocusEvent) {
  if (day.value !== date) selectDay(date)
  const input = event.target as HTMLInputElement
  input.select()
}

/** 只暂存与已记录值不同的输入；空记录与已记录的零分开处理。 */
function editDay(date: string, event: Event) {
  const value = (event.target as HTMLInputElement).value
  if (value === String(props.stats.days[date] ?? '')) delete drafts.value[date]
  else drafts.value[date] = value
  notice.value = error.value = ''
}

/** 取消或 Esc 一并丢弃各月草稿，保留选中日期；无草稿时 Esc 才关闭日历。 */
function cancel() {
  if (saving.value) return
  if (!pending.value.length) return dialog.value?.close()
  drafts.value = {}
  error.value = notice.value = ''
}

/** 一次提交所有变更日期；服务端整批写回后才清空草稿，失败可原样重试。 */
async function saveDays() {
  if (!canSave.value) return
  saving.value = true
  error.value = ''
  try {
    const totals = Object.fromEntries(pending.value.map(([date, value]) => [date, Number(value)]))
    const stats: DropStats = await api.patch(props.endpoint, { totals })
    emit('updated', stats)
    drafts.value = {}
    notice.value = t('已保存')
  } catch (exception: unknown) {
    error.value = exception instanceof Error ? exception.message : t('保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="field">
    <div class="field-label">
      <div class="fname">{{ t('修改每日掉落') }}</div>
      <div class="fhelp save-dir">{{ t('保存目录') }}: {{ stats.save_dir }}</div>
    </div>
    <div class="field-control">
      <button ref="trigger" type="button" class="btn" aria-haspopup="dialog" :disabled="loading" @click="openEditor">
        <AppIcon name="calendar" :size="14" /> {{ t(loading ? '读取中…' : '打开日历') }}
      </button>
    </div>
  </div>
  <Teleport to="body">
    <dialog ref="dialog" class="drop-dialog" aria-labelledby="drop-editor-title" @cancel.prevent="cancel" @close="trigger?.focus()" @click.self="!saving && !pending.length && dialog?.close()">
      <form class="modal-card calendar-card" :aria-busy="saving" @submit.prevent="saveDays">
        <header class="calendar-header">
          <h3 id="drop-editor-title">{{ t('修改每日掉落') }}</h3>
          <button class="btn calendar-icon" type="button" :aria-label="t('关闭')" :title="t('关闭')" :disabled="saving || pending.length > 0" @click="dialog?.close()"><AppIcon name="x" :size="15" /></button>
        </header>
        <p class="calendar-hint">{{ t('在日期格内修改掉落总数，可跨月编辑后一次保存。') }}</p>
        <div class="month-bar">
          <strong aria-live="polite">{{ monthTitle }}</strong>
          <div class="month-actions">
            <button type="button" class="btn sm" :disabled="saving || month === currentMonth" @click="showMonth()">{{ t('本月') }}</button>
            <button type="button" class="btn calendar-icon" :aria-label="t('上个月')" :title="t('上个月')" :disabled="saving" @click="showMonth(-1)"><AppIcon class="previous" name="arrow-right" :size="14" /></button>
            <button type="button" class="btn calendar-icon" :aria-label="t('下个月')" :title="t('下个月')" :disabled="saving || month >= currentMonth" @click="showMonth(1)"><AppIcon name="arrow-right" :size="14" /></button>
          </div>
        </div>
        <div class="calendar-grid" role="group" :aria-label="monthTitle">
          <span v-for="weekday in weekdays" :key="weekday" class="weekday">{{ weekday }}</span>
          <span v-for="i in offset" :key="`blank-${i}`" aria-hidden="true"></span>
          <label v-for="cell in days" :key="cell.date" class="day-cell" :class="{ today: cell.date === stats.today, selected: cell.date === day, future: cell.future, modified: cell.date in drafts }">
            <span class="day-number">{{ cell.number }}</span>
            <input v-if="!cell.future" class="day-input" type="text" inputmode="numeric" autocomplete="off" placeholder="—"
              :aria-label="`${cell.date} · ${t('当天掉落总数')}`" :aria-current="cell.date === stats.today ? 'date' : undefined"
              :aria-invalid="cell.date in drafts && !validCount(drafts[cell.date])" :disabled="saving"
              :value="drafts[cell.date] ?? cell.total ?? ''" @focus="focusDay(cell.date, $event)"
              @input="editDay(cell.date, $event)" @keydown.enter.prevent="saveDays">
          </label>
        </div>
        <footer class="selection-panel">
          <div class="selection-summary" aria-live="polite">
            <time :datetime="day">{{ day }}</time>
            <span>{{ t('已记录总数') }}: <strong>{{ recorded ?? t('无记录') }}</strong></span>
          </div>
          <div class="modal-actions calendar-actions">
            <span role="status" class="calendar-status" :class="{ invalid: error || invalidDay }">{{
              error || (invalidDay ? `${invalidDay} · ${t('请输入非负整数。')}` : pending.length ? `${t('待保存天数')}: ${pending.length}` : notice)
            }}</span>
            <button type="button" class="btn" :disabled="saving || !pending.length" @click="cancel">{{ t('取消') }}</button>
            <button type="submit" class="btn primary" :disabled="!canSave">{{ t(saving ? '保存中…' : '保存') }}</button>
          </div>
        </footer>
      </form>
    </dialog>
  </Teleport>
</template>

<style scoped>
.drop-dialog { margin:auto; max-width:none; max-height:none; padding:0; border:0; background:transparent; color:var(--text); overflow:visible; }
.drop-dialog::backdrop { background:rgba(0,0,0,.45); }
.calendar-card { display:flex; flex-direction:column; width:min(548px,calc(100vw - 24px)); max-height:calc(100vh - 24px); max-height:calc(100dvh - 24px); overflow:auto; }
.calendar-card > :not(.calendar-grid) { flex:none; }
.calendar-header,.month-bar,.month-actions { display:flex; align-items:center; }
.calendar-header,.month-bar { justify-content:space-between; gap:4px; }
.calendar-header h3 { margin:0; }
.calendar-hint { margin-top:4px; color:var(--text-2); font-size:12.5px; line-height:1.5; }
.month-bar { margin:16px 0 8px; padding-top:14px; border-top:1px solid var(--border); }
.month-actions { gap:6px; }
.calendar-icon { width:32px; height:32px; padding:0; flex:none; }
.previous { transform:rotate(180deg); }
.calendar-grid { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:4px; min-height:0; overflow-y:auto; }
.weekday { padding:3px 0; color:var(--text-2); font-size:12px; text-align:center; }
.day-cell { display:flex; flex-direction:column; justify-content:space-between; gap:2px; min-width:0; height:57px; padding:5px 7px; border:1px solid var(--border-light); border-radius:8px; color:var(--text); background:var(--button-bg); text-align:left; font-variant-numeric:tabular-nums; }
.day-cell:not(.future):hover,.day-cell.selected { border-color:var(--accent); background:var(--accent-soft); }
.day-cell:focus-within { outline:2px solid var(--accent); outline-offset:-2px; }
.day-number { align-self:flex-start; font-size:12px; line-height:18px; color:var(--text-2); }
.modified .day-number::after { content:''; display:inline-block; width:5px; height:5px; margin-left:3px; border-radius:50%; background:var(--accent); vertical-align:middle; }
.today .day-number { padding:0 5px; border-radius:6px; background:var(--accent-soft); color:var(--accent-2); font-weight:700; }
.day-input { width:100%; min-width:0; height:22px; padding:0; border:0; outline:0; background:transparent; color:var(--text); font-size:16px; font-weight:600; }
.day-input::placeholder,.future .day-number { color:var(--text-3); font-weight:400; }
.day-input[aria-invalid="true"] { color:var(--red); }
.day-cell.future { border-color:transparent; background:var(--card-2); }
.selection-panel { margin-top:12px; padding-top:12px; border-top:1px solid var(--border); }
.selection-summary { display:flex; flex-wrap:wrap; align-items:baseline; justify-content:space-between; gap:4px 12px; color:var(--text-2); font-size:13px; font-variant-numeric:tabular-nums; }
.selection-summary time,.selection-summary strong { color:var(--text); font-weight:600; }
.calendar-actions { align-items:center; margin-top:8px; }
.calendar-actions .btn { flex:none; }
.calendar-status { margin-right:auto; color:var(--text-2); font-size:12px; overflow-wrap:anywhere; }
.calendar-status.invalid { color:var(--red); }
.save-dir { overflow-wrap:anywhere; }
@media (max-width:540px) {
  .calendar-card { padding:16px 12px; }
  .calendar-grid { gap:3px; }
  .day-cell { height:52px; padding:4px; }
}
@media (max-height:650px) {
  .day-cell { height:44px; padding-block:2px; }
}
</style>
