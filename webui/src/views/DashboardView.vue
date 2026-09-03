<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { GridItem, GridLayout } from 'grid-layout-plus'
import AppIcon from '../components/AppIcon.vue'
import AccountInfoCard from '../components/AccountInfoCard.vue'
import DailyTasksCard from '../components/DailyTasksCard.vue'
import EventCalendar from '../components/EventCalendar.vue'
import { t } from '../i18n'
import { useDashboardLayoutStore } from '../stores/dashboardLayout'
import { useInstancesStore } from '../stores/instances'
import { useSystemStore } from '../stores/system'
import { useToastStore } from '../stores/toast'
import { useUiStore } from '../stores/ui'

const instancesStore = useInstancesStore()
const { instances, runningCount, loaded } = storeToRefs(instancesStore)
const { displayStatus, displayStatusClass, initials, avatarUrl, lifecycle } = instancesStore
const { systemStatus } = storeToRefs(useSystemStore())
const dashboard = useDashboardLayoutStore()
const { editing, layout, visibleLayout, narrow } = storeToRefs(dashboard)
const { enter } = useUiStore()
const toast = useToastStore()
const grid = ref<InstanceType<typeof GridLayout> | null>(null)
let resizeObserver: ResizeObserver | undefined

const instanceByName = computed(() => new Map(instances.value.map(instance => [instance.name, instance])))
function calendarError(message: string) { toast.error = message }
function itemName(item: { i: string }) { return item.i.startsWith('instance:') ? item.i.slice('instance:'.length) : '' }
function itemInstance(item: { i: string }) { return instanceByName.value.get(itemName(item)) }
function itemType(item: { i: string }) { return item.i === 'stats' ? 'stats' : item.i === 'instances' ? 'instances' : item.i === 'calendar' ? 'calendar' : 'instance' }
function itemTitle(item: { i: string }) {
  const type = itemType(item)
  if (type === 'stats') return t('运行概况')
  if (type === 'instances') return t('实例')
  if (type === 'calendar') return t('活动日历')
  return `${t('实例概览')} · ${itemName(item)}`
}
function onLayoutUpdated(nextLayout: any[]) {
  // 窄屏单列布局是派生视图，不写回持久化布局
  if (narrow.value) return
  // 内容一致时不要整体替换：新数组会再次触发 grid 的 layout-updated，形成死循环
  const current = layout.value
  const changed = nextLayout.length !== current.length || nextLayout.some((item, index) => {
    const previous = current[index]
    return !previous || item.i !== previous.i || item.x !== previous.x || item.y !== previous.y || item.w !== previous.w || item.h !== previous.h
  })
  if (changed) layout.value = nextLayout.map(item => ({ ...item }))
}

// 分区默认按内容高度撑开（不换行出滚动条）；用户在编辑态手动拖过高度后不再跟随内容
const customSized = new Set<string>()
// 缓存各分区最近一次内容高度：编辑态切换时分区头显隐改变所需高度，需要重算
const lastFit = new Map<string, [number, number, boolean]>()
function fitSectionHeight(id: string, contentHeight: number, minH: number, hasInnerTitle = false) {
  lastFit.set(id, [contentHeight, minH, hasInnerTitle])
  if (narrow.value || customSized.has(id)) return
  // 网格行高 17 + 纵向间距 18（每行 35）
  // 编辑态需额外装下分区头与内边距；展示态日历标题在内容内部，其余分区标题是独立分区头
  const extra = editing.value && !narrow.value ? 71 : (hasInnerTitle ? 18 : 35)
  // n 行实际高度是 35n-18（末行后无 margin），所以 +18 再向上取行数
  const nextHeight = Math.max(minH, Math.ceil((contentHeight + extra + 18) / 35))
  const index = layout.value.findIndex(item => item.i === id)
  if (index < 0 || layout.value[index].h === nextHeight) return
  layout.value = layout.value.map((item, itemIndex) => itemIndex === index ? { ...item, h: nextHeight } : item)
}
watch(editing, () => { for (const [id, args] of lastFit) fitSectionHeight(id, ...args) })
function onCalendarHeight(height: number) { fitSectionHeight('calendar', height, 12, true) }
function onInstancesHeight(height: number) { fitSectionHeight('instances', height, 8) }
// 运行概况统计卡内容随实例数变化，用 ResizeObserver 跟随
let statsObserver: ResizeObserver | undefined
function setStatsContent(el: Element | null) {
  statsObserver?.disconnect()
  if (!el) return
  statsObserver = new ResizeObserver(entries => fitSectionHeight('stats', entries[0].target.scrollHeight, 4))
  statsObserver.observe(el)
  fitSectionHeight('stats', (el as HTMLElement).scrollHeight, 4)
}
// 实例概览：测量内容列（卡片）的真实高度——容器高度被分区约束，scrollHeight 会高估
// 内容随实例异步加载挂载，需要登记元素后在实例列表就绪时重量
const summaryEls = new Map<string, Element>()
function measureSummary(el: Element, id: string) {
  const contentHeight = Math.max(0, ...[...el.children].map(child => (child as HTMLElement).offsetHeight))
  fitSectionHeight(id, contentHeight, 6)
}
function setSummaryContent(el: Element | null, id: string) {
  if (el) { summaryEls.set(id, el); measureSummary(el, id) }
  else summaryEls.delete(id)
}
// 实例区内容（卡片网格）高度随换行变化，ResizeObserver 需要观测内容元素本身：
// 父级滚动容器尺寸被分区高度固定，scrollHeight 变化不会触发回调
let instancesObserver: ResizeObserver | undefined
function setInstancesContent(el: Element | null) {
  instancesObserver?.disconnect()
  if (!el) return
  instancesObserver = new ResizeObserver(entries => onInstancesHeight(entries[0].target.scrollHeight))
  instancesObserver.observe(el)
  onInstancesHeight((el as HTMLElement).scrollHeight)
}
function onItemResized(id: string) { customSized.add(id) }
function onResetLayout() {
  customSized.clear()
  dashboard.resetLayout()
}

function updateResponsiveState() {
  const width = (grid.value as any)?.$el?.offsetWidth || window.innerWidth
  dashboard.setNarrow(width <= 1200)
}

onMounted(() => {
  dashboard.reconcile(instances.value.map(instance => instance.name), !loaded.value)
  resizeObserver = new ResizeObserver(updateResponsiveState)
  const element = (grid.value as any)?.$el as HTMLElement | undefined
  if (element) resizeObserver.observe(element)
  updateResponsiveState()
})
watch([loaded, () => instances.value.map(instance => instance.name)], async ([isLoaded, names]) => {
  dashboard.reconcile(names, !isLoaded)
  await nextTick()
  summaryEls.forEach(measureSummary)
}, { deep: true })
onBeforeUnmount(() => { resizeObserver?.disconnect(); instancesObserver?.disconnect() })
</script>

<template>
  <section class="view dashboard-view">
    <div class="dashboard-fabs">
      <template v-if="editing && !narrow">
        <button v-for="entry in dashboard.hidden" :key="entry.i" class="dashboard-fab dashboard-fab-wide" type="button" :title="t('恢复显示')" @click="dashboard.show(entry.i)"><AppIcon name="plus" :size="13" color="currentColor" /> {{ itemTitle(entry) }}</button>
        <button class="dashboard-fab danger" type="button" :title="t('重置布局')" :aria-label="t('重置布局')" @click="onResetLayout"><AppIcon name="refresh" :size="16" color="currentColor" /></button>
        <button class="dashboard-fab primary" type="button" :title="t('保存')" :aria-label="t('保存')" @click="dashboard.toggleEdit"><AppIcon name="check" :size="16" /></button>
      </template>
      <button v-else-if="!narrow" class="dashboard-fab" type="button" :aria-label="t('编辑布局')" :title="t('编辑布局')" @click="dashboard.toggleEdit"><AppIcon name="edit" :size="16" /></button>
    </div>

    <GridLayout
      ref="grid"
      :layout="visibleLayout"
      class="dashboard-grid"
      :class="{ 'dashboard-grid-editing': editing && !narrow }"
      :col-num="narrow ? 1 : 12"
      :row-height="17"
      :margin="[18, 18]"
      :auto-size="true"
      :is-draggable="editing && !narrow"
      :is-resizable="editing && !narrow"
      :vertical-compact="true"
      :responsive="false"
      @layout-updated="onLayoutUpdated"
    >
      <GridItem v-for="item in visibleLayout" :key="item.i" v-bind="item" drag-allow-from=".drag-handle" class="dashboard-grid-item" :class="[{ 'is-editing': editing && !narrow }, `dashboard-grid-item--${itemType(item)}`]" @resized="onItemResized(item.i)">
        <article class="card dashboard-zone">
          <header class="dashboard-zone-head" :class="{ 'drag-handle': editing && !narrow }">
            <span class="dashboard-zone-grip" aria-hidden="true"><AppIcon v-if="editing && !narrow" name="sort-v" :size="13" /></span>
            <strong>{{ itemTitle(item) }}</strong>
            <button v-if="editing && !narrow" class="dashboard-hide-btn" type="button" :title="t('隐藏')" @click="dashboard.hide(item.i)"><AppIcon name="eye-off" :size="13" /></button>
          </header>

          <div v-if="itemType(item) === 'stats'" class="dashboard-zone-body dashboard-stats-body">
            <div class="stat-row" :ref="setStatsContent">
              <article class="card stat-card"><div class="stat-icon blue"><AppIcon name="monitor" :size="22" /></div><div><div class="stat-num">{{ instances.length }}</div><div class="stat-lbl">{{ t('实例总数') }}</div></div></article>
              <article class="card stat-card"><div class="stat-icon green"><AppIcon name="play" :size="22" /></div><div><div class="stat-num" style="color:var(--green)">{{ runningCount }}</div><div class="stat-lbl">{{ t('运行中') }}</div></div></article>
            </div>
          </div>

          <div v-else-if="itemType(item) === 'instances'" class="dashboard-zone-body dashboard-scroll-body">
            <div v-if="!instances.length" :ref="setInstancesContent" class="dashboard-empty"><strong>{{ t('暂无实例') }}</strong><span>{{ t('请前往多开页面新建实例') }}</span></div>
            <div v-else :ref="setInstancesContent" class="inst-grid">
              <article v-for="instance in instances" :key="instance.name" class="card inst-card hoverable" :class="{ 'is-running': displayStatusClass(instance.name, instance.state, instance.current_task) === 'running' }">
                <div class="inst-card-head">
                  <span class="inst-avatar" :class="{ idle: displayStatusClass(instance.name, instance.state, instance.current_task) === 'idle' }"><img v-if="avatarUrl(instance.avatar)" class="inst-avatar-img" :src="avatarUrl(instance.avatar)" :alt="instance.name"><template v-else>{{ initials(instance.name) }}</template><span class="ring" :class="displayStatusClass(instance.name, instance.state, instance.current_task)"></span></span>
                  <div><h3>{{ instance.name }}</h3><div v-if="instance.mod !== 'nkas'" class="sub">mod: {{ instance.mod }}</div></div>
                  <span class="status-pill" :class="displayStatusClass(instance.name, instance.state, instance.current_task)" style="margin-left:auto"><span v-if="displayStatusClass(instance.name, instance.state, instance.current_task) === 'running'" class="pulse"></span>{{ displayStatus(instance.name, instance.state, instance.current_task) }}</span>
                </div>
                <div class="inst-now"><span class="k">{{ t('当前任务') }}</span><span>{{ instance.current_task || t('无') }}</span></div>
                <div class="inst-now"><span class="k">{{ t('下一任务') }}</span><span>{{ instance.next_task || '—' }}</span></div>
                <div class="inst-card-foot">
                  <button class="btn sm" :class="instance.state === 1 ? 'danger' : 'success'" style="flex:1" @click="lifecycle(instance.state === 1 ? 'stop' : 'start', instance.name)">{{ instance.state === 1 ? t('停止') : t('启动') }}</button>
                  <button class="btn primary sm" style="flex:1" @click="enter(instance.name)">{{ t('进入') }} <AppIcon name="arrow-right" :size="14" /></button>
                </div>
              </article>
            </div>
          </div>

          <div v-else-if="itemType(item) === 'calendar'" class="dashboard-zone-body dashboard-scroll-body dashboard-calendar-body">
            <EventCalendar :language="systemStatus.language" :show-title="!editing || narrow" @error="calendarError" @height="onCalendarHeight" />
          </div>

          <div v-else class="dashboard-zone-body dashboard-instance-summary" :ref="(el) => setSummaryContent(el as Element | null, item.i)">
            <template v-if="itemInstance(item)">
              <AccountInfoCard :instance-name="itemName(item)" />
              <DailyTasksCard :instance-name="itemName(item)" />
            </template>
          </div>
        </article>
      </GridItem>
    </GridLayout>
  </section>
</template>
