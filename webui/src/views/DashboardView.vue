<script setup lang="ts">
import { storeToRefs } from 'pinia'
import EventCalendar from '../components/EventCalendar.vue'
import { t } from '../i18n'
import { useInstancesStore } from '../stores/instances'
import { useModalStore } from '../stores/modal'
import { useSystemStore } from '../stores/system'
import { useToastStore } from '../stores/toast'
import { useUiStore } from '../stores/ui'

const instancesStore = useInstancesStore()
const { instances, runningCount } = storeToRefs(instancesStore)
const { displayStatus, displayStatusClass, initials, lifecycle } = instancesStore
const { systemStatus } = storeToRefs(useSystemStore())
const { enter } = useUiStore()
const { openCreateModal } = useModalStore()
const toast = useToastStore()
function calendarError(message: string) { toast.error = message }
</script>

<template>
  <section class="view">
    <div class="stat-row">
      <article class="card stat-card hoverable"><div class="stat-icon blue">🖥️</div><div><div class="stat-num">{{ instances.length }}</div><div class="stat-lbl">{{ t('实例总数') }}</div></div></article>
      <article class="card stat-card hoverable"><div class="stat-icon green">▶️</div><div><div class="stat-num" style="color:var(--green)">{{ runningCount }}</div><div class="stat-lbl">{{ t('运行中') }}</div></div></article>
    </div>
    <div class="section-title">{{ t('实例') }}</div>
    <div class="inst-grid">
      <article v-for="instance in instances" :key="instance.name" class="card inst-card hoverable" :class="{ 'is-running': displayStatusClass(instance.name, instance.state, instance.current_task) === 'running' }">
        <div class="inst-card-head">
          <span class="inst-avatar" :class="{ idle: displayStatusClass(instance.name, instance.state, instance.current_task) === 'idle' }">{{ initials(instance.name) }}<span class="ring" :class="displayStatusClass(instance.name, instance.state, instance.current_task)"></span></span>
          <div><h3>{{ instance.name }}</h3><div v-if="instance.mod !== 'nkas'" class="sub">mod: {{ instance.mod }}</div></div>
          <span class="status-pill" :class="displayStatusClass(instance.name, instance.state, instance.current_task)" style="margin-left:auto"><span v-if="displayStatusClass(instance.name, instance.state, instance.current_task) === 'running'" class="pulse"></span>{{ displayStatus(instance.name, instance.state, instance.current_task) }}</span>
        </div>
        <div class="inst-now"><span class="k">{{ t('当前任务') }}</span><span>{{ instance.current_task || t('无') }}</span></div>
        <div class="inst-now"><span class="k">{{ t('下一任务') }}</span><span>{{ instance.next_task || '—' }}</span></div>
        <div class="inst-card-foot">
          <button class="btn sm" :class="instance.state === 1 ? 'danger' : 'success'" style="flex:1" @click="lifecycle(instance.state === 1 ? 'stop' : 'start', instance.name)">{{ instance.state === 1 ? t('停止') : t('启动') }}</button>
          <button class="btn primary sm" style="flex:1" @click="enter(instance.name)">{{ t('进入 →') }}</button>
        </div>
      </article>
      <button class="card add-card" @click="openCreateModal"><span class="plus">＋</span>{{ t('新建实例') }}</button>
    </div>
    <EventCalendar :language="systemStatus.language" @error="calendarError" />
  </section>
</template>
