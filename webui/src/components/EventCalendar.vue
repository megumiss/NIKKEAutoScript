<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import AppIcon from './AppIcon.vue'
import { api } from '../api/client'

type CalendarItem = {
  id: string
  category: string
  type: string
  subtype?: string | null
  title: string
  subtitle?: string
  start_time: number
  end_time: number
  stage_start_time?: number
  stage_end_time?: number
  source_order: number
  banner_mode: 'image' | 'pass_composite' | 'placeholder'
  banner_url?: string
  character_url?: string
  background_url?: string
}

const props = defineProps<{ language: string }>()
const emit = defineEmits<{ error: [message: string]; height: [height: number] }>()

const labels: Record<string, Record<string, string>> = {
  '活动日历': { 'en-US': 'Activity calendar', 'ja-JP': 'イベントカレンダー' },
  '全部': { 'en-US': 'All', 'ja-JP': 'すべて' },
  '招募': { 'en-US': 'Recruitment', 'ja-JP': '募集' },
  '超频': { 'en-US': 'Overclock', 'ja-JP': 'オーバークロック' },
  '时装': { 'en-US': 'Costumes', 'ja-JP': 'コスチューム' },
  '剧情活动': { 'en-US': 'Story events', 'ja-JP': 'ストーリーイベント' },
  '竞技场': { 'en-US': 'Arena', 'ja-JP': 'アリーナ' },
  '刷新': { 'en-US': 'Refresh', 'ja-JP': '更新' },
  '更新于': { 'en-US': 'Updated', 'ja-JP': '更新' },
  '开始': { 'en-US': 'Starts', 'ja-JP': '開始' },
  '结束': { 'en-US': 'Ends', 'ja-JP': '終了' },
  '剩余': { 'en-US': 'Remaining', 'ja-JP': '残り' },
  '距离Buff重置': { 'en-US': 'Until Buff reset', 'ja-JP': 'バフリセットまで' },
  '即将结束': { 'en-US': 'Ending soon', 'ja-JP': 'まもなく終了' },
  '最后6小时': { 'en-US': 'Final 6 hours', 'ja-JP': '残り6時間' },
  '暂无进行中的活动': { 'en-US': 'No active events', 'ja-JP': '開催中のイベントはありません' },
  '活动数据加载失败': { 'en-US': 'Unable to load activity data', 'ja-JP': 'イベントデータを読み込めません' },
  '不足1分钟': { 'en-US': 'Less than 1 minute', 'ja-JP': '1分未満' },
  '天': { 'en-US': 'd', 'ja-JP': '日' },
  '小时': { 'en-US': 'h', 'ja-JP': '時間' },
  '分钟': { 'en-US': 'm', 'ja-JP': '分' },
  '转盘': { 'en-US': 'Roulette', 'ja-JP': 'ルーレット' },
}

const tabs = [
  { key: 'all', label: '全部' },
  { key: 'character_gacha', label: '招募' },
  { key: 'raid', label: 'Raid' },
  { key: 'simulation_room', label: '超频' },
  { key: 'skin_gacha', label: '时装' },
  { key: 'version_event', label: '剧情活动' },
  { key: 'arena', label: '竞技场' },
]
const categoryOrder: Record<string, number> = Object.fromEntries(tabs.slice(1).map((tab, index) => [tab.key, index]))
const items = ref<CalendarItem[]>([])
const activeTab = ref('all')
const loading = ref(true)
const refreshing = ref(false)
const loadFailed = ref(false)
const updatedAt = ref(0)
const now = ref(Math.floor(Date.now() / 1000))
const failedBanners = ref<Record<string, boolean>>({})
let clockTimer: number | undefined
let contentObserver: ResizeObserver | undefined
const calendarRoot = ref<HTMLElement | null>(null)

function reportHeight() {
  if (calendarRoot.value) emit('height', calendarRoot.value.scrollHeight)
}

function t(source: string) {
  return props.language === 'zh-CN' ? source : labels[source]?.[props.language] || source
}

function subtypeLabel(subtype?: string | null) {
  if (subtype === 'roulette') return t('转盘')
  if (subtype === 'pass') return 'PASS'
  return subtype || ''
}

function categoryLabel(category: string) {
  return t(tabs.find(tab => tab.key === category)?.label || category)
}

const visibleItems = computed(() => items.value
  .filter(item => item.end_time > now.value && (activeTab.value === 'all' || item.category === activeTab.value))
  .sort((left, right) => left.end_time - right.end_time
    || (categoryOrder[left.category] ?? 99) - (categoryOrder[right.category] ?? 99)
    || left.start_time - right.start_time
    || left.source_order - right.source_order))

function formatDateTime(timestamp: number) {
  if (!timestamp) return '—'
  return new Intl.DateTimeFormat(props.language || 'zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(timestamp * 1000))
}

function remainingSeconds(item: CalendarItem) {
  return Math.max(0, item.end_time - now.value)
}

function remainingText(item: CalendarItem) {
  return remainingTextUntil(item.end_time)
}

function remainingTextUntil(timestamp: number) {
  const seconds = Math.max(0, timestamp - now.value)
  if (seconds < 60) return t('不足1分钟')
  const days = Math.floor(seconds / 86400)
  const hours = Math.floor((seconds % 86400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const parts = []
  if (days) parts.push(`${days}${t('天')}`)
  if (hours || days) parts.push(`${hours}${t('小时')}`)
  if (!days) parts.push(`${minutes}${t('分钟')}`)
  return parts.join(' ')
}

function urgency(item: CalendarItem) {
  const seconds = remainingSeconds(item)
  if (seconds <= 6 * 3600) return 'critical'
  if (seconds <= 24 * 3600) return 'soon'
  return ''
}

function urgencyLabel(item: CalendarItem) {
  return urgency(item) === 'critical' ? t('最后6小时') : urgency(item) === 'soon' ? t('即将结束') : ''
}

function bannerFailed(item: CalendarItem) {
  failedBanners.value[item.id] = true
}

async function loadCalendar(forceRefresh = false) {
  if (forceRefresh) refreshing.value = true
  else if (!items.value.length) loading.value = true
  try {
    const params = new URLSearchParams({ language: props.language || 'zh-CN' })
    if (forceRefresh) params.set('refresh', '1')
    const result = await api.get(`/api/calendar?${params.toString()}`)
    items.value = Array.isArray(result.items) ? result.items : []
    updatedAt.value = Number(result.updated_at || 0)
    failedBanners.value = {}
    loadFailed.value = false
    now.value = Math.floor(Date.now() / 1000)
  } catch (exception: any) {
    loadFailed.value = !items.value.length
    emit('error', exception.message || t('活动数据加载失败'))
  } finally {
    loading.value = false
    refreshing.value = false
    await nextTick()
    reportHeight()
  }
}

onMounted(() => {
  loadCalendar()
  contentObserver = new ResizeObserver(reportHeight)
  if (calendarRoot.value) contentObserver.observe(calendarRoot.value)
  reportHeight()
  clockTimer = window.setInterval(() => {
    const previous = now.value
    now.value = Math.floor(Date.now() / 1000)
    const crossedStageBoundary = items.value.some(item => item.stage_end_time
      && previous < item.stage_end_time && now.value >= item.stage_end_time)
    if (crossedStageBoundary) loadCalendar()
  }, 60 * 1000)
})

watch(() => props.language, () => loadCalendar())
watch(activeTab, async () => {
  await nextTick()
  reportHeight()
})
onBeforeUnmount(() => {
  window.clearInterval(clockTimer)
  contentObserver?.disconnect()
})
</script>

<template>
  <section ref="calendarRoot" class="event-calendar">
    <header class="event-calendar-head">
      <div>
        <div v-if="updatedAt" class="event-updated">{{ t('更新于') }} {{ formatDateTime(updatedAt) }}</div>
      </div>
      <button class="btn sm event-refresh" type="button" :disabled="refreshing" :title="t('刷新')" @click="loadCalendar(true)">
        <span class="event-refresh-icon" :class="{ spinning: refreshing }" aria-hidden="true"><AppIcon name="refresh" :size="14" /></span>
        {{ t('刷新') }}
      </button>
    </header>

    <div class="event-tabs" role="tablist" :aria-label="t('活动日历')">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        role="tab"
        class="event-tab"
        :class="{ active: activeTab === tab.key }"
        :aria-selected="activeTab === tab.key"
        @click="activeTab = tab.key"
      >{{ t(tab.label) }}</button>
    </div>

    <div v-if="loading" class="event-grid" aria-busy="true">
      <article v-for="index in 3" :key="index" class="event-card event-skeleton">
        <div class="event-skeleton-banner"></div>
        <div class="event-skeleton-line wide"></div>
        <div class="event-skeleton-line"></div>
      </article>
    </div>

    <div v-else-if="loadFailed" class="event-empty">{{ t('活动数据加载失败') }}</div>
    <div v-else-if="!visibleItems.length" class="event-empty">{{ t('暂无进行中的活动') }}</div>

    <div v-else class="event-grid">
      <article
        v-for="item in visibleItems"
        :key="item.id"
        class="event-card"
        :class="urgency(item) ? `urgency-${urgency(item)}` : ''"
      >
        <div class="event-banner">
          <img
            v-if="item.banner_mode === 'image' && item.banner_url && !failedBanners[item.id]"
            :src="item.banner_url"
            :alt="item.title"
            loading="lazy"
            referrerpolicy="no-referrer"
            @error="bannerFailed(item)"
          />
          <div
            v-else-if="item.banner_mode === 'pass_composite' && !failedBanners[item.id]"
            class="event-pass-banner"
            :style="item.background_url ? { backgroundImage: `url(${item.background_url})` } : undefined"
          >
            <img v-if="item.character_url" class="event-pass-character" :src="item.character_url" alt="" loading="lazy" referrerpolicy="no-referrer" @error="bannerFailed(item)" />
            <img v-if="item.banner_url" class="event-pass-logo" :src="item.banner_url" :alt="item.title" loading="lazy" referrerpolicy="no-referrer" @error="bannerFailed(item)" />
          </div>
          <div v-else class="event-banner-placeholder"><span>{{ categoryLabel(item.category) }}</span></div>
          <span class="event-category">{{ categoryLabel(item.category) }}</span>
          <span v-if="urgencyLabel(item)" class="event-urgency">{{ urgencyLabel(item) }}</span>
        </div>

        <div class="event-card-body">
          <div class="event-title-row">
            <h3>{{ item.title }}</h3>
            <span v-if="item.subtype" class="event-subtype">{{ subtypeLabel(item.subtype) }}</span>
          </div>
          <div v-if="item.subtitle" class="event-subtitle">{{ item.subtitle }}</div>
          <dl class="event-times">
            <div><dt>{{ t('开始') }}</dt><dd>{{ formatDateTime(item.start_time) }}</dd></div>
            <div><dt>{{ t('结束') }}</dt><dd>{{ formatDateTime(item.end_time) }}</dd></div>
            <div class="event-remaining"><dt>{{ t('剩余') }}</dt><dd>{{ remainingText(item) }}</dd></div>
            <div v-if="item.stage_end_time" class="event-stage-reset"><dt>{{ t('距离Buff重置') }}</dt><dd>{{ remainingTextUntil(item.stage_end_time) }}</dd></div>
          </dl>
        </div>
      </article>
    </div>
  </section>
</template>

<style scoped>
.event-calendar { margin-top:0; padding-top:2px; }
.event-calendar-head { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:14px; }
.event-updated { margin-top:4px; color:var(--text-3); font-size:11.5px; }
.event-refresh { display:inline-flex; align-items:center; gap:7px; }
.event-refresh-icon { display:inline-block; font-size:17px; line-height:1; }
.event-refresh-icon.spinning { animation:event-spin 1s linear infinite; }
.event-tabs { display:flex; gap:7px; margin-bottom:16px; overflow-x:auto; padding-bottom:2px; }
.event-tab { height:34px; flex:none; padding:0 14px; border:1px solid var(--button-border); border-radius:8px; color:var(--text-2); background:var(--button-bg); font-size:12.5px; }
.event-tab:hover { border-color:var(--button-border-hover); color:var(--accent); background:var(--button-bg-hover); }
.event-tab.active { border-color:var(--accent); color:var(--accent); background:var(--accent-soft); font-weight:700; }
.event-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(360px,1fr)); gap:18px; }
.event-card { min-width:0; overflow:hidden; border:1px solid var(--border); border-radius:8px; background:var(--card); box-shadow:var(--shadow); transition:border-color .16s,box-shadow .16s,transform .16s; }
.event-card:hover { border-color:var(--border-light); box-shadow:var(--shadow-hover); transform:translateY(-2px); }
.event-card.urgency-soon { border-color:rgba(255,193,120,.75); box-shadow:0 0 0 1px var(--yellow-soft),var(--shadow); }
.event-card.urgency-critical { border-color:rgba(239,111,111,.8); box-shadow:0 0 0 1px var(--red-soft),var(--shadow); }
.event-banner { position:relative; width:100%; aspect-ratio:3 / 1; overflow:hidden; border-bottom:1px solid var(--border); background:var(--card-2); }
.event-banner > img { display:block; width:100%; height:100%; object-fit:cover; }
.event-category,.event-urgency { position:absolute; top:10px; padding:4px 9px; border-radius:7px; color:#fff; background:rgba(15,18,24,.78); font-size:11px; font-weight:700; line-height:1.2; backdrop-filter:blur(4px); }
.event-category { left:10px; }
.event-urgency { right:10px; color:#21160b; background:var(--yellow); }
.urgency-critical .event-urgency { color:#fff; background:var(--red); }
.event-banner-placeholder { display:grid; width:100%; height:100%; place-items:center; color:var(--text-3); background:var(--card-2); }
.event-banner-placeholder span { padding:8px 14px; border:1px solid var(--border-light); border-radius:8px; font-size:13px; font-weight:700; }
.event-pass-banner { position:relative; width:100%; height:100%; overflow:hidden; background-color:#161c27; background-position:center; background-size:cover; }
.event-pass-character { position:absolute; top:-18%; left:0; width:48%; height:154%; object-fit:cover; object-position:top center; }
.event-pass-logo { position:absolute; top:10%; right:5%; width:48%; height:80%; object-fit:contain; }
.event-card-body { padding:15px 16px 16px; }
.event-title-row { display:flex; gap:10px; align-items:flex-start; }
.event-title-row h3 { min-width:0; flex:1; overflow-wrap:anywhere; font-size:15px; line-height:1.35; }
.event-subtype { flex:none; padding:3px 8px; border-radius:7px; color:var(--accent); background:var(--accent-soft); font-size:10.5px; font-weight:700; }
.event-subtitle { min-height:18px; margin-top:4px; overflow:hidden; color:var(--text-3); font-size:11.5px; text-overflow:ellipsis; white-space:nowrap; }
.event-times { margin-top:12px; border-top:1px solid var(--border); }
.event-times > div { display:flex; justify-content:space-between; gap:16px; padding-top:8px; font-size:12px; }
.event-times dt { color:var(--text-3); }
.event-times dd { color:var(--text-2); text-align:right; }
.event-remaining dd { color:var(--green); font-weight:700; }
.event-stage-reset { margin-top:4px; border-top:1px dashed var(--border); }
.event-stage-reset dd { color:var(--accent); font-weight:700; }
.urgency-soon .event-remaining dd { color:var(--yellow); }
.urgency-critical .event-remaining dd { color:var(--red); }
.event-empty { display:grid; min-height:150px; place-items:center; border:1px dashed var(--border-light); border-radius:8px; color:var(--text-3); background:var(--card-2); font-size:13px; }
.event-skeleton { padding-bottom:16px; }
.event-skeleton-banner,.event-skeleton-line { background:var(--card-2); animation:event-loading 1.2s ease-in-out infinite alternate; }
.event-skeleton-banner { width:100%; aspect-ratio:3 / 1; border-bottom:1px solid var(--border); }
.event-skeleton-line { width:45%; height:10px; margin:12px 16px 0; border-radius:5px; }
.event-skeleton-line.wide { width:68%; height:14px; }
@keyframes event-spin { to { transform:rotate(360deg); } }
@keyframes event-loading { to { opacity:.45; } }
@media (max-width:1200px) { .event-grid { grid-template-columns:repeat(auto-fill,minmax(320px,1fr)); } }
</style>
