<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const ScrcpyPlayer = defineAsyncComponent(() => import('./ScrcpyPlayer.vue'))

const props = withDefaults(defineProps<{ name: string; language: string; closable?: boolean }>(), { closable: false })
const emit = defineEmits<{ (e: 'close'): void }>()

const labels: Record<string, Record<string, string>> = {
  '画面预览': { 'en-US': 'Screen preview', 'ja-JP': '画面プレビュー' },
  '实时': { 'en-US': 'Live', 'ja-JP': 'リアルタイム' },
  '待机': { 'en-US': 'Idle', 'ja-JP': '待機中' },
  '暂无画面': { 'en-US': 'No screen yet', 'ja-JP': '画面がありません' },
  '刷新': { 'en-US': 'Refresh', 'ja-JP': '更新' },
  '刷新频率': { 'en-US': 'Refresh rate', 'ja-JP': '更新間隔' },
  '控制': { 'en-US': 'Control', 'ja-JP': '操作' },
  '操作栏': { 'en-US': 'Control bar', 'ja-JP': '操作バー' },
  '退出控制': { 'en-US': 'Exit control', 'ja-JP': '操作を終了' },
  '关闭': { 'en-US': 'Close', 'ja-JP': '閉じる' },
}

function t(source: string) {
  return props.language === 'zh-CN' ? source : labels[source]?.[props.language] || source
}

type PreviewStatus = 'none' | 'live' | 'stale'

const frameUrl = ref('')
const status = ref<PreviewStatus>('none')
const LIVE_WINDOW_SECONDS = 5
const POLL_RATES = [1, 2, 5, 10]
const RATE_STORAGE_KEY = 'nkas-preview-rate'
const rateIndex = ref(Math.max(0, POLL_RATES.indexOf(Number(localStorage.getItem(RATE_STORAGE_KEY)) || 1)))
let pollTimer: number | undefined
let lastCapturedAt = 0
let capturedAt = 0

const pollRate = computed(() => POLL_RATES[rateIndex.value])
const staleAfter = computed(() => Math.max(LIVE_WINDOW_SECONDS, pollRate.value + 2))

function cycleRate() {
  rateIndex.value = (rateIndex.value + 1) % POLL_RATES.length
  localStorage.setItem(RATE_STORAGE_KEY, String(pollRate.value))
  startPolling()
}

const bodyEl = ref<HTMLElement>()
const rawW = ref(720)
const rawH = ref(1280)
const showControlBar = ref(false)
const wrapW = ref(0)
const wrapH = ref(0)
const frameWrapStyle = computed(() => ({ width: `${wrapW.value}px`, height: `${wrapH.value}px` }))
const BODY_PADDING = 24
let resizeObserver: ResizeObserver | undefined

function measure() {
  const body = bodyEl.value
  if (!body || !interactive.value) return
  const aspect = rawW.value / rawH.value
  const extraW = showControlBar.value ? 44 : 0
  const availableW = Math.max(extraW, body.clientWidth - BODY_PADDING)
  const availableH = Math.max(0, body.clientHeight - BODY_PADDING)
  let w = Math.round(availableH * aspect) + extraW
  let h = Math.round(availableH)
  if (w > availableW) {
    w = Math.round(availableW)
    h = Math.max(0, Math.round((w - extraW) / aspect))
  }
  wrapW.value = w
  wrapH.value = h
}

function onStreamSize(size: { width: number; height: number }) {
  rawW.value = size.width
  rawH.value = size.height
  measure()
}

function startObserver() {
  if (!bodyEl.value) return
  resizeObserver = new ResizeObserver(measure)
  resizeObserver.observe(bodyEl.value)
  measure()
}

const statusText = computed(() => status.value === 'live' ? t('实时') : t('待机'))
const refreshing = ref(false)
const interactive = ref(false)
const controlTitle = computed(() => interactive.value ? t('退出控制') : t('控制'))

async function toggleInteractive() {
  interactive.value = !interactive.value
  showControlBar.value = false
  if (interactive.value) stopPolling()
  else startPolling()
  await nextTick()
  measure()
}

function toggleControlBar() {
  showControlBar.value = !showControlBar.value
  measure()
}

async function refresh() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    await tick()
  } finally {
    refreshing.value = false
  }
}

function setFrame(url: string) {
  if (frameUrl.value) URL.revokeObjectURL(frameUrl.value)
  frameUrl.value = url
}

async function tick() {
  if (!props.name || interactive.value) return
  const name = props.name
  try {
    const response = await fetch(`/api/${encodeURIComponent(props.name)}/screenshot?t=${Date.now()}`)
    if (name !== props.name || interactive.value) return
    if (response.ok) {
      const at = Number(response.headers.get('X-Captured-At') || 0)
      if (at && at !== lastCapturedAt) {
        lastCapturedAt = at
        capturedAt = at
        const blob = await response.blob()
        if (name !== props.name || interactive.value) return
        setFrame(URL.createObjectURL(blob))
      }
      status.value = capturedAt && Date.now() / 1000 - capturedAt <= staleAfter.value ? 'live' : 'stale'
    } else if (response.status === 404) {
      status.value = 'none'
    }
  } catch {
    // Network hiccup: keep the current frame and retry on the next tick.
  }
}

function startPolling() {
  stopPolling()
  tick()
  pollTimer = window.setInterval(tick, pollRate.value * 1000)
}

function stopPolling() {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

function resetFrame() {
  lastCapturedAt = 0
  capturedAt = 0
  status.value = 'none'
  if (frameUrl.value) {
    URL.revokeObjectURL(frameUrl.value)
    frameUrl.value = ''
  }
}

watch(() => props.name, () => {
  resetFrame()
  interactive.value = false
  startPolling()
})

onMounted(() => {
  startPolling()
  startObserver()
})

onBeforeUnmount(() => {
  stopPolling()
  resizeObserver?.disconnect()
  if (frameUrl.value) URL.revokeObjectURL(frameUrl.value)
})
</script>

<template>
  <article class="card preview-card">
    <div class="preview-head">
      <b>{{ t('画面预览') }}</b>
      <span v-if="frameUrl && !interactive" class="preview-badge" :class="status">{{ statusText }}</span>
      <span class="preview-icons">
        <button class="preview-icon" :class="{ 'control-active': interactive }" type="button" :aria-pressed="interactive" :aria-label="controlTitle" :title="controlTitle" @click="toggleInteractive">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/><path d="M13 13l6 6"/></svg>
        </button>
        <button v-if="interactive" class="preview-icon" :class="{ 'control-active': showControlBar }" type="button" :aria-label="t('操作栏')" :title="t('操作栏')" @click="toggleControlBar">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/></svg>
        </button>
        <template v-if="!interactive">
          <button class="preview-rate" type="button" :title="t('刷新频率')" @click="cycleRate">{{ pollRate }}s</button>
          <button class="preview-icon" :class="{ spinning: refreshing }" type="button" :aria-label="t('刷新')" :title="t('刷新')" @click="refresh">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
          </button>
        </template>
        <button v-if="closable" class="preview-icon preview-modal-close" type="button" :aria-label="t('关闭')" :title="t('关闭')" @click="emit('close')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>
        </button>
      </span>
    </div>
    <div ref="bodyEl" class="preview-body">
      <div v-if="interactive" class="preview-frame-wrap" :style="frameWrapStyle">
        <ScrcpyPlayer :key="name" :name="name" :language="language" :show-controls="showControlBar" @size="onStreamSize" />
      </div>
      <img v-else-if="frameUrl" :src="frameUrl" :alt="t('画面预览')">
      <div v-else class="preview-empty">{{ t('暂无画面') }}</div>
    </div>
  </article>
</template>

<style scoped>
.preview-card { display:flex; flex-direction:column; width:100%; height:100%; min-height:0; overflow:hidden; }
.preview-head { display:flex; flex:none; gap:10px; align-items:center; min-height:54px; padding:13px 18px; border-bottom:1px solid var(--border); }
.preview-head > b { white-space:nowrap; }
.preview-badge { padding:2px 9px; border-radius:7px; font-size:11px; font-weight:700; }
.preview-badge.live { color:var(--green); background:var(--green-soft); }
.preview-badge.stale { color:var(--text-3); background:var(--card-3); }
.preview-icons { margin-left:auto; display:flex; flex:none; gap:6px; align-items:center; }
.preview-icon { display:inline-flex; align-items:center; justify-content:center; width:24px; height:24px; padding:3px; border:0; border-radius:5px; color:var(--text-3); background:transparent; cursor:pointer; }
.preview-icon svg { display:block; width:16px; height:16px; }
.preview-icon:hover { color:var(--text); background:var(--card-2); }
.preview-icon:disabled { opacity:.35; cursor:not-allowed; }
.preview-icon.control-active { color:var(--accent); }
.preview-icon.spinning { animation:preview-spin .8s linear infinite; }
.preview-rate { min-width:34px; padding:3px 6px; border:1px solid var(--border); border-radius:7px; color:var(--text-2); background:transparent; font-size:11.5px; font-weight:700; cursor:pointer; }
.preview-rate:hover { border-color:var(--accent); color:var(--accent); }
.preview-body { display:flex; flex:1; align-items:center; justify-content:center; min-height:0; padding:12px; overflow:hidden; background:var(--log-bg); }
.preview-body img { display:block; max-width:100%; max-height:100%; border-radius:6px; object-fit:contain; }
.preview-frame-wrap { position:relative; overflow:hidden; border-radius:6px; background:#000; }
.preview-empty { color:var(--text-3); font-size:13px; }
@keyframes preview-spin { to { transform:rotate(360deg); } }
@media (prefers-reduced-motion:reduce) { .preview-icon.spinning { animation:none; } }
</style>
