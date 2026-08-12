<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps<{ name: string; language: string }>()

const labels: Record<string, Record<string, string>> = {
  '画面预览': { 'en-US': 'Screen preview', 'ja-JP': '画面プレビュー' },
  '实时': { 'en-US': 'Live', 'ja-JP': 'リアルタイム' },
  '待机': { 'en-US': 'Idle', 'ja-JP': '待機中' },
  '暂无画面': { 'en-US': 'No screen yet', 'ja-JP': '画面がありません' },
  '刷新': { 'en-US': 'Refresh', 'ja-JP': '更新' },
  '刷新频率': { 'en-US': 'Refresh rate', 'ja-JP': '更新間隔' },
}

function t(source: string) {
  return props.language === 'zh-CN' ? source : labels[source]?.[props.language] || source
}

type PreviewStatus = 'none' | 'live' | 'stale'

const expanded = ref(false)
const frameUrl = ref('')
const status = ref<PreviewStatus>('none')
// Frames older than this many seconds are reported as idle instead of live.
const LIVE_WINDOW_SECONDS = 5
// Polling cadence options (seconds); cycled by the rate button, persisted.
const POLL_RATES = [1, 2, 5, 10]
const RATE_STORAGE_KEY = 'nkas-preview-rate'
const rateIndex = ref(Math.max(0, POLL_RATES.indexOf(Number(localStorage.getItem(RATE_STORAGE_KEY)) || 1)))
let pollTimer: number | undefined
let lastCapturedAt = 0
let capturedAt = 0

const pollRate = computed(() => POLL_RATES[rateIndex.value])
// With slower polling a fresh frame can legitimately be older than the base
// live window, so the idle threshold follows the cadence.
const staleAfter = computed(() => Math.max(LIVE_WINDOW_SECONDS, pollRate.value + 2))

function cycleRate() {
  rateIndex.value = (rateIndex.value + 1) % POLL_RATES.length
  localStorage.setItem(RATE_STORAGE_KEY, String(pollRate.value))
  if (expanded.value) startPolling()
}

// Wide screens use a row layout where the card stretches to the panel height;
// derive the card width from the body height and the frame's real aspect ratio
// so the image fills the card without empty bands. Narrow screens stack
// vertically and fall back to full-width CSS sizing.
const bodyEl = ref<HTMLElement>()
const frameAspect = ref(720 / 1280)
const cardWidth = ref(0)
let resizeObserver: ResizeObserver | undefined
const BODY_PADDING_X = 24

function onFrameLoad(event: Event) {
  const img = event.target as HTMLImageElement
  if (img.naturalWidth && img.naturalHeight) frameAspect.value = img.naturalWidth / img.naturalHeight
}

function measure() {
  const body = bodyEl.value
  if (!body || window.innerWidth <= 1200) {
    cardWidth.value = 0
    return
  }
  // clientHeight 含 12px 上下内边距，图片可用高度需扣除
  cardWidth.value = Math.round((body.clientHeight - BODY_PADDING_X) * frameAspect.value) + BODY_PADDING_X
}

function startObserver() {
  stopObserver()
  if (!bodyEl.value) return
  resizeObserver = new ResizeObserver(measure)
  resizeObserver.observe(bodyEl.value)
  measure()
}

function stopObserver() {
  resizeObserver?.disconnect()
  resizeObserver = undefined
}

const statusText = computed(() => status.value === 'live' ? t('实时') : t('待机'))
const refreshing = ref(false)

// Manual refresh: force an immediate fetch outside the 1s polling cadence.
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
  if (!expanded.value || !props.name) return
  try {
    const response = await fetch(`/api/${encodeURIComponent(props.name)}/screenshot?t=${Date.now()}`)
    if (response.ok) {
      const at = Number(response.headers.get('X-Captured-At') || 0)
      // Skip the body when the frame has not changed; the stream is discarded.
      if (at && at !== lastCapturedAt) {
        lastCapturedAt = at
        capturedAt = at
        setFrame(URL.createObjectURL(await response.blob()))
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

watch(expanded, async value => {
  if (value) {
    startPolling()
    await nextTick()
    startObserver()
  } else {
    stopPolling()
    stopObserver()
  }
})

watch(frameAspect, measure)

watch(() => props.name, () => {
  resetFrame()
  if (expanded.value) startPolling()
})

onBeforeUnmount(() => {
  stopPolling()
  stopObserver()
  if (frameUrl.value) URL.revokeObjectURL(frameUrl.value)
})
</script>

<template>
  <article v-if="expanded" class="card preview-card" :style="cardWidth ? { width: `${cardWidth}px` } : undefined">
    <div class="preview-head">
      <b>{{ t('画面预览') }}</b>
      <span v-if="frameUrl" class="preview-badge" :class="status">{{ statusText }}</span>
      <span class="preview-icons">
        <button class="preview-rate" type="button" :title="t('刷新频率')" @click="cycleRate">{{ pollRate }}s</button>
        <button class="preview-icon" :class="{ spinning: refreshing }" type="button" :title="t('刷新')" @click="refresh">↻</button>
        <button class="preview-icon preview-toggle" type="button" :title="t('画面预览')" @click="expanded = false">›</button>
      </span>
    </div>
    <div ref="bodyEl" class="preview-body">
      <img v-if="frameUrl" :src="frameUrl" :alt="t('画面预览')" @load="onFrameLoad">
      <div v-else class="preview-empty">{{ t('暂无画面') }}</div>
    </div>
  </article>
  <button v-else class="card preview-strip" type="button" :title="t('画面预览')" @click="expanded = true">
    <span class="preview-strip-arrow">‹</span>
    <span class="preview-strip-text">{{ t('画面预览') }}</span>
  </button>
</template>

<style scoped>
.preview-card { display:flex; flex-direction:column; width:clamp(240px, 22vw, 380px); flex:none; min-height:0; overflow:hidden; }
.preview-head { display:flex; gap:10px; align-items:center; padding:13px 18px; border-bottom:1px solid var(--border); }
.preview-badge { padding:2px 9px; border-radius:7px; font-size:11px; font-weight:700; }
.preview-badge.live { color:var(--green); background:var(--green-soft); }
.preview-badge.stale { color:var(--text-3); background:var(--card-3); }
.preview-icon { padding:0 4px; border:0; color:var(--text-3); background:transparent; font-size:16px; line-height:1; cursor:pointer; }
.preview-icon:hover { color:var(--text); }
.preview-icon.spinning { animation:preview-spin .8s linear infinite; }
/* 刷新按钮是第一个图标按钮，把整组推到头部右侧 */
.preview-icons { margin-left:auto; display:flex; gap:6px; align-items:center; }
.preview-rate { min-width:34px; padding:2px 6px; border:1px solid var(--border); border-radius:7px; color:var(--text-2); background:transparent; font-size:11.5px; font-weight:700; cursor:pointer; }
.preview-rate:hover { border-color:var(--accent); color:var(--accent); }
@keyframes preview-spin { to { transform:rotate(360deg); } }
.preview-body { display:flex; flex:1; align-items:center; justify-content:center; min-height:0; padding:12px; overflow:hidden; background:var(--log-bg); }
.preview-body img { display:block; max-width:100%; max-height:100%; border-radius:6px; object-fit:contain; }
.preview-empty { color:var(--text-3); font-size:13px; }
.preview-strip { display:flex; width:40px; flex:none; flex-direction:column; gap:10px; align-items:center; padding:14px 0; cursor:pointer; }
.preview-strip:hover { border-color:var(--accent); }
.preview-strip-arrow { color:var(--text-3); font-size:15px; }
.preview-strip:hover .preview-strip-arrow { color:var(--accent); }
.preview-strip-text { color:var(--text-2); font-size:13px; letter-spacing:.15em; writing-mode:vertical-rl; }
@media (max-width:1200px) {
  .preview-card { width:100%; max-height:70vh; }
  .preview-strip { width:100%; height:40px; flex-direction:row; justify-content:center; padding:0 14px; }
  .preview-strip-text { letter-spacing:.08em; writing-mode:horizontal-tb; }
}
</style>
