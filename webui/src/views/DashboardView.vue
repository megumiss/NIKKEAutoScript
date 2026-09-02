<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
const { instances, runningCount } = storeToRefs(instancesStore)
const { displayStatus, displayStatusClass, initials, avatarUrl, lifecycle } = instancesStore
const { systemStatus } = storeToRefs(useSystemStore())
const dashboard = useDashboardLayoutStore()
const { editing, layout, narrow } = storeToRefs(dashboard)
const { enter } = useUiStore()
const toast = useToastStore()
const grid = ref<InstanceType<typeof GridLayout> | null>(null)
let resizeObserver: ResizeObserver | undefined

const instanceByName = computed(() => new Map(instances.value.map(instance => [instance.name, instance])))
const renderLayout = computed(() => editing.value
  ? layout.value
  : [...layout.value].sort((left, right) => left.y - right.y || left.x - right.x))
function calendarError(message: string) { toast.error = message }
function itemName(item: { i: string }) { return item.i.startsWith('instance:') ? item.i.slice('instance:'.length) : '' }
function itemInstance(item: { i: string }) { return instanceByName.value.get(itemName(item)) }
function itemType(item: { i: string }) { return item.i === 'stats' ? 'stats' : item.i === 'instances' ? 'instances' : item.i === 'calendar' ? 'calendar' : 'instance' }
function onLayoutUpdated(nextLayout: any[]) {
  const next = nextLayout.map(item => ({ ...item }))
  const current = layout.value
  const changed = next.length !== current.length || next.some((item, index) => {
    const previous = current[index]
    return !previous || item.i !== previous.i || item.x !== previous.x || item.y !== previous.y || item.w !== previous.w || item.h !== previous.h
  })
  if (changed) layout.value = next
}
function onCalendarHeight(height: number) {
  const nextHeight = Math.max(12, Math.ceil((height + 42) / 34))
  const index = layout.value.findIndex(item => item.i === 'calendar')
  if (index < 0 || layout.value[index].h === nextHeight) return
  layout.value = layout.value.map((item, itemIndex) => itemIndex === index ? { ...item, h: nextHeight } : item)
}
function updateResponsiveState() {
  const width = (grid.value as any)?.$el?.offsetWidth || window.innerWidth
  dashboard.setNarrow(width <= 960)
}

onMounted(() => {
  dashboard.reconcile(instances.value.map(instance => instance.name))
  resizeObserver = new ResizeObserver(updateResponsiveState)
  const element = (grid.value as any)?.$el as HTMLElement | undefined
  if (element) resizeObserver.observe(element)
  updateResponsiveState()
})
watch(() => instances.value.map(instance => instance.name), names => dashboard.reconcile(names), { deep: true })
onBeforeUnmount(() => resizeObserver?.disconnect())
</script>

<template>
  <section class="view dashboard-view">
    <div class="dashboard-toolbar">
      <div>
        <div class="section-title dashboard-title">{{ t('总览') }}</div>
        <div class="dashboard-hint">{{ narrow ? t('窄屏已切换为单列') : t('横屏布局可自定义') }}</div>
      </div>
      <div class="dashboard-actions">
        <button class="btn sm" type="button" @click="dashboard.resetLayout"><AppIcon name="refresh" :size="14" /> {{ t('重置布局') }}</button>
        <button v-if="!narrow" class="btn primary sm" type="button" :aria-pressed="editing" @click="dashboard.toggleEdit"><AppIcon :name="editing ? 'check' : 'designtools'" :size="14" /> {{ editing ? t('完成') : t('编辑布局') }}</button>
      </div>
    </div>

    <GridLayout
      ref="grid"
      v-model:layout="layout"
      class="dashboard-grid"
      :class="{ 'dashboard-grid-static': !editing || narrow, 'dashboard-grid-editing': editing && !narrow }"
      :col-num="12"
      :row-height="34"
      :margin="[18, 0]"
      :auto-size="true"
      :is-draggable="editing && !narrow"
      :is-resizable="editing && !narrow"
      :is-bounded="false"
      :vertical-compact="false"
      :responsive="false"
      @layout-updated="onLayoutUpdated"
    >
      <GridItem v-for="item in renderLayout" :key="item.i" v-bind="item" class="dashboard-grid-item" :class="{ 'is-editing': editing && !narrow }">
        <article class="card dashboard-zone" :class="{ 'is-static': !editing || narrow }">
          <header class="dashboard-zone-head" :class="{ 'drag-handle': editing && !narrow }">
            <span class="dashboard-zone-grip" aria-hidden="true"><AppIcon v-if="editing && !narrow" name="sort-v" :size="13" /></span>
            <template v-if="itemType(item) === 'stats'"><strong>{{ t('运行概况') }}</strong></template>
            <template v-else-if="itemType(item) === 'instances'"><strong>{{ t('实例') }}</strong></template>
            <template v-else-if="itemType(item) === 'calendar'"><strong>{{ t('活动日历') }}</strong></template>
            <template v-else><strong>{{ itemName(item) }} · {{ t('实例概览') }}</strong></template>
          </header>

          <div v-if="itemType(item) === 'stats'" class="dashboard-zone-body dashboard-stats-body">
            <div class="stat-row">
              <article class="card stat-card"><div class="stat-icon blue"><AppIcon name="monitor" :size="22" /></div><div><div class="stat-num">{{ instances.length }}</div><div class="stat-lbl">{{ t('实例总数') }}</div></div></article>
              <article class="card stat-card"><div class="stat-icon green"><AppIcon name="play" :size="22" /></div><div><div class="stat-num" style="color:var(--green)">{{ runningCount }}</div><div class="stat-lbl">{{ t('运行中') }}</div></div></article>
            </div>
          </div>

          <div v-else-if="itemType(item) === 'instances'" class="dashboard-zone-body dashboard-scroll-body">
            <div v-if="!instances.length" class="dashboard-empty"><strong>{{ t('暂无实例') }}</strong><span>{{ t('请前往多开页面新建实例') }}</span></div>
            <div v-else class="inst-grid">
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
            <EventCalendar :language="systemStatus.language" @error="calendarError" @height="onCalendarHeight" />
          </div>

          <div v-else class="dashboard-zone-body dashboard-instance-summary">
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
