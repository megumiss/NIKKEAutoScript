<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import LiveLog from '../components/LiveLog.vue'
import ScreenPreview from '../components/ScreenPreview.vue'
import { t } from '../i18n'
import { dayOf, formatDate, formatTime } from '../utils'
import { useInstancesStore } from '../stores/instances'
import { useSystemStore } from '../stores/system'
import { useWorkspaceStore } from '../stores/workspace'

const workspace = useWorkspaceStore()
const { queue, selectedName } = storeToRefs(workspace)
const { openQueueItem } = workspace
const instancesStore = useInstancesStore()
const { lifecycle } = instancesStore
const { systemStatus } = storeToRefs(useSystemStore())
const selectedInstance = computed(() => instancesStore.instances.find(item => item.name === selectedName.value))
</script>

<template>
  <section class="view">
    <div class="ov-layout">
      <div class="ov-left">
        <article class="card hero-sched">
          <div style="flex:1"><b>{{ t('调度器') }}</b></div>
          <button class="btn" :class="selectedInstance?.state === 1 ? 'danger' : 'success'" @click="lifecycle(selectedInstance?.state === 1 ? 'stop' : 'start')">{{ selectedInstance?.state === 1 ? t('停止') : t('启动') }}</button>
        </article>
        <article class="card queue-card">
          <div class="timeline">
            <div class="tl-label">{{ t('运行中') }}</div>
            <div v-for="item in queue.running || []" :key="item.command" class="tl-item running clickable" @click="openQueueItem(item)">{{ item.name_i18n }}<span class="t">{{ formatTime(item.next_run) }}</span><span class="go">›</span></div>
            <div v-if="!queue.running?.length" class="tl-item placeholder">{{ t('暂无运行任务') }}</div>
            <div class="tl-label">{{ t('队列中') }}</div>
            <div v-for="item in queue.pending || []" :key="item.command" class="tl-item clickable" @click="openQueueItem(item)">{{ item.name_i18n }}<span class="t">{{ formatTime(item.next_run) }}</span><span class="go">›</span></div>
            <div v-if="!queue.pending?.length" class="tl-item placeholder">{{ t('队列为空') }}</div>
          </div>
        </article>
        <article class="card queue-card">
          <div class="queue-group-label">{{ t('等待中') }}</div>
          <div class="timeline">
            <template v-for="(item, index) in queue.waiting || []" :key="item.command">
              <div v-if="index > 0 && dayOf(item.next_run) !== dayOf(queue.waiting[index - 1].next_run)" class="tl-date-sep"><span>{{ formatDate(item.next_run) }}</span></div>
              <div class="tl-item clickable" @click="openQueueItem(item)">{{ item.name_i18n }}<span class="t">{{ formatTime(item.next_run) }}</span><span class="go">›</span></div>
            </template>
          </div>
        </article>
      </div>
      <div class="ov-right">
        <LiveLog />
        <ScreenPreview :name="selectedName" :language="systemStatus.language" />
      </div>
    </div>
  </section>
</template>
