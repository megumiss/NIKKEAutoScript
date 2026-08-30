<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import AppIcon from './AppIcon.vue'
import { api } from '../api/client'

type Notice = {
  post_uuid: string
  start_time: number
  end_time: number
  official_period?: string
  fetched_at?: number
  title?: string
}

const props = defineProps<{ language: string }>()

const labels: Record<string, Record<string, string>> = {
  '维护通知': { 'en-US': 'Maintenance notice', 'ja-JP': 'メンテナンスのお知らせ' },
  '维护进行中': { 'en-US': 'Maintenance in progress', 'ja-JP': 'メンテナンス中' },
  '维护时间': { 'en-US': 'Maintenance time', 'ja-JP': 'メンテナンス時間' },
  '距维护开始': { 'en-US': 'Starts in', 'ja-JP': '開始まで' },
  '预计结束': { 'en-US': 'Expected end', 'ja-JP': '終了予定' },
  '关闭': { 'en-US': 'Close', 'ja-JP': '閉じる' },
  '天': { 'en-US': 'd', 'ja-JP': '日' },
  '小时': { 'en-US': 'h', 'ja-JP': '時間' },
  '分钟': { 'en-US': 'm', 'ja-JP': '分' },
}

function t(source: string) {
  return props.language === 'zh-CN' ? source : labels[source]?.[props.language] || source
}

const notice = ref<Notice | null>(null)
// Close state is intentionally kept in memory only, so the banner reappears
// on every app launch — a maintenance reminder must not be silently missed.
const dismissed = ref(false)
const now = ref(Math.floor(Date.now() / 1000))

let clockTimer: number | undefined
let refreshTimer: number | undefined

const visible = computed(() => {
  if (!notice.value || dismissed.value) return false
  // Banner clears as soon as the cached period ends; the next poll picks up a
  // revised or new notice.
  return now.value < notice.value.end_time
})

const inProgress = computed(() => {
  const current = notice.value
  return Boolean(current && now.value >= current.start_time && now.value < current.end_time)
})

function formatDateTime(timestamp: number) {
  const locale = props.language || 'zh-CN'
  const date = new Date(timestamp * 1000)
  const md = new Intl.DateTimeFormat(locale, { month: 'numeric', day: 'numeric' }).format(date)
  const weekday = new Intl.DateTimeFormat(locale, { weekday: 'short' }).format(date)
  const time = new Intl.DateTimeFormat(locale, { hour: '2-digit', minute: '2-digit', hour12: false }).format(date)
  return `${md}（${weekday}）${time}`
}

function remainingTextUntil(timestamp: number) {
  const seconds = Math.max(0, timestamp - now.value)
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const parts = []
  if (days) parts.push(`${days}${t('天')}`)
  if (hours || days) parts.push(`${hours}${t('小时')}`)
  if (!days) parts.push(`${minutes}${t('分钟')}`)
  return parts.join(' ')
}

const localRange = computed(() => {
  const current = notice.value
  if (!current) return ''
  return `${formatDateTime(current.start_time)} ~ ${formatDateTime(current.end_time)}`
})

const localTimeZone = computed(() => {
  // getTimezoneOffset() is "UTC − local" in minutes, so negate it.  Keep the
  // minute part for half/quarter-hour zones (e.g. UTC+5:30, UTC-3:30).
  const minutes = -new Date().getTimezoneOffset()
  const sign = minutes >= 0 ? '+' : '-'
  const abs = Math.abs(minutes)
  const hours = Math.floor(abs / 60)
  const rest = abs % 60
  return `UTC${sign}${hours}${rest ? `:${String(rest).padStart(2, '0')}` : ''}`
})

const startsInText = computed(() => remainingTextUntil(notice.value?.start_time || 0))

async function fetchNotice() {
  try {
    const data = await api.get('/api/maintenance')
    notice.value = data.notice || null
  } catch {
    // Keep the previous notice on transient failures; the backend also serves
    // the last cached entry when possible.
  }
}

onMounted(() => {
  fetchNotice()
  clockTimer = window.setInterval(() => { now.value = Math.floor(Date.now() / 1000) }, 60 * 1000)
  // Quiet refresh; the backend decides whether to hit BlaBlaLink.
  refreshTimer = window.setInterval(fetchNotice, 10 * 60 * 1000)
})

onBeforeUnmount(() => {
  if (clockTimer !== undefined) window.clearInterval(clockTimer)
  if (refreshTimer !== undefined) window.clearInterval(refreshTimer)
})
</script>

<template>
  <div v-if="visible" class="maintenance-notice">
    <span class="mn-title">{{ inProgress ? t('维护进行中') : t('维护通知') }}</span>
    <span class="mn-sep">·</span>
    <span class="mn-period">{{ t('维护时间') }}：{{ localRange }}（{{ localTimeZone }}）</span>
    <span class="mn-sep">·</span>
    <span v-if="!inProgress" class="mn-left">{{ t('距维护开始') }} {{ startsInText }}</span>
    <span v-else class="mn-left">{{ t('预计结束') }} {{ formatDateTime(notice?.end_time || 0) }}</span>
    <button type="button" class="mn-close" :title="t('关闭')" @click="dismissed = true"><AppIcon name="x" :size="14" /></button>
  </div>
</template>

<style scoped>
.maintenance-notice {
  position: absolute;
  z-index: 5;
  top: 68px;
  left: 50%;
  display: flex;
  gap: 8px;
  align-items: center;
  width: fit-content;
  max-width: min(760px, calc(100vw - 48px));
  padding: 10px 14px;
  border-radius: 12px;
  color: #fff;
  background: var(--red);
  box-shadow: var(--shadow-hover);
  transform: translateX(-50%);
  font-size: 13px;
  animation: mn-in .25s ease both;
}
:root[data-theme='dark'] .maintenance-notice {
  background: #c23f3f;
}
.maintenance-notice > span {
  white-space: nowrap;
}
.mn-title {
  flex: none;
  font-weight: 700;
}
.mn-sep {
  flex: none;
  opacity: .55;
}
.mn-period {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
.mn-left {
  flex: none;
  font-weight: 600;
}
.mn-close {
  display: grid;
  width: 22px;
  height: 22px;
  margin-left: 4px;
  flex: none;
  place-items: center;
  border: 0;
  border-radius: 6px;
  color: rgba(255, 255, 255, .92);
  background: rgba(255, 255, 255, .16);
  font-size: 12px;
  line-height: 1;
}
.mn-close:hover {
  color: #fff;
  background: rgba(255, 255, 255, .32);
}
@keyframes mn-in {
  from { opacity: 0; transform: translate(-50%, 6px); }
  to { opacity: 1; transform: translate(-50%, 0); }
}
</style>
