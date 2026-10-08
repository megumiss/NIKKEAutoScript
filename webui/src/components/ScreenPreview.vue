<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const ScrcpyPlayer = defineAsyncComponent(() => import('./ScrcpyPlayer.vue'))

const props = defineProps<{ name: string; language: string }>()

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
  '仅 adb 可用': { 'en-US': 'Only available over adb', 'ja-JP': 'adb のみ利用可能' },
}

function t(source: string) {
  return props.language === 'zh-CN' ? source : labels[source]?.[props.language] || source
}

type PreviewStatus = 'none' | 'live' | 'stale'

// Open/closed state is persisted locally so a reload keeps the preview as
// the user left it; the polling cadence below is persisted the same way.
const EXPANDED_STORAGE_KEY = 'nkas-preview-expanded'
const expanded = ref(localStorage.getItem(EXPANDED_STORAGE_KEY) === '1')
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

const rawW = ref(720)
const rawH = ref(1280)
const showControlBar = ref(false)
const wrapW = ref(0)
const wrapH = ref(0)
const frameWrapStyle = computed(() => ({ width: `${wrapW.value}px`, height: `${wrapH.value}px` }))

function onStreamSize(size: { width: number; height: number }) {
  rawW.value = size.width
  rawH.value = size.height
  measure()
}

function onFrameLoad(event: Event) {
  const img = event.target as HTMLImageElement
  if (img.naturalWidth && img.naturalHeight) frameAspect.value = img.naturalWidth / img.naturalHeight
}

function measure() {
  const body = bodyEl.value
  if (!body) {
    cardWidth.value = 0
    return
  }
  if (!interactive.value) {
    // clientHeight 含 12px 上下内边距，图片可用高度需扣除
    if (window.innerWidth <= 1200) {
      cardWidth.value = 0
    } else {
      cardWidth.value = Math.round((body.clientHeight - BODY_PADDING_X) * frameAspect.value) + BODY_PADDING_X
    }
    return
  }
  // 互动模式：遮罩 = 视频区（按真实宽高比从高度推出）+ 可选控制栏
  const aspect = rawW.value / rawH.value
  const extraW = showControlBar.value ? 44 : 0
  if (window.innerWidth <= 1200) {
    cardWidth.value = 0
    const headH = body.previousElementSibling?.getBoundingClientRect().height || 48
    const maxH = Math.max(0, window.innerHeight * 0.7 - headH - BODY_PADDING_X)
    wrapW.value = Math.max(extraW, Math.round(Math.min(body.clientWidth - BODY_PADDING_X, maxH * aspect + extraW)))
    wrapH.value = Math.round((wrapW.value - extraW) / aspect)
  } else {
    wrapH.value = Math.round(body.clientHeight - BODY_PADDING_X)
    let w = Math.round(wrapH.value * aspect) + extraW
    // 预览卡与日志卡共享 .ov-right 横向空间（日志卡 flex:1、min-width:0，
    // 会被无限制挤压），横向流按高度推出的宽度可能吃掉整行——表现为卡片
    // 先撑满再退回。把宽度钳制在容器的一定比例内，超出就按宽度反推高度。
    // 注意不能按 body.clientWidth 钳制：body 宽度由卡片宽度决定，会正反馈收缩。
    const parent = body.parentElement?.parentElement
    const maxW = parent ? Math.round(parent.clientWidth * 0.55) : w
    if (w > maxW) {
      w = maxW
      wrapH.value = Math.max(0, Math.round((w - extraW) / aspect))
    }
    wrapW.value = w
    cardWidth.value = wrapW.value + BODY_PADDING_X
  }
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
  if (!expanded.value || !props.name || interactive.value) return
  const name = props.name
  try {
    const response = await fetch(`/api/${encodeURIComponent(props.name)}/screenshot?t=${Date.now()}`)
    if (name !== props.name || !expanded.value || interactive.value) return
    if (response.ok) {
      const at = Number(response.headers.get('X-Captured-At') || 0)
      // Skip the body when the frame has not changed; the stream is discarded.
      if (at && at !== lastCapturedAt) {
        lastCapturedAt = at
        capturedAt = at
        const blob = await response.blob()
        if (name !== props.name || !expanded.value || interactive.value) return
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

async function openPreview() {
  startPolling()
  await nextTick()
  startObserver()
}

function closePreview() {
  interactive.value = false
  stopPolling()
  stopObserver()
}

watch(expanded, value => {
  localStorage.setItem(EXPANDED_STORAGE_KEY, value ? '1' : '0')
  if (value) openPreview()
  else closePreview()
})

// The watcher is not immediate, so when the persisted state reopens the
// preview right on mount the polling/layout tracking must start here.
onMounted(() => {
  if (expanded.value) openPreview()
})

watch(frameAspect, measure)

watch(() => props.name, () => {
  resetFrame()
  interactive.value = false
  if (expanded.value) {
    startPolling()
  }
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
      <span v-if="frameUrl && !interactive" class="preview-badge" :class="status">{{ statusText }}</span>
      <span class="preview-icons">
        <button class="preview-icon" :class="{ 'control-active': interactive }" type="button" :aria-pressed="interactive" :aria-label="controlTitle" :title="controlTitle" @click="toggleInteractive">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3l7.07 16.97 2.51-7.39 7.39-2.51L3 3z"/><path d="M13 13l6 6"/></svg>
        </button>
        <button v-if="interactive" class="preview-icon" :class="{ 'control-active': showControlBar }" type="button" :title="t('操作栏')" @click="toggleControlBar">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="1"/><circle cx="12" cy="5" r="1"/><circle cx="12" cy="19" r="1"/></svg>
        </button>
        <template v-if="!interactive">
          <button class="preview-rate" type="button" :title="t('刷新频率')" @click="cycleRate">{{ pollRate }}s</button>
          <button class="preview-icon" :class="{ spinning: refreshing }" type="button" :title="t('刷新')" @click="refresh">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
          </button>
        </template>
        <button class="preview-icon preview-toggle" type="button" :title="t('画面预览')" @click="expanded = false">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
        </button>
      </span>
    </div>
    <div ref="bodyEl" class="preview-body">
      <div v-if="interactive" class="preview-frame-wrap" :style="frameWrapStyle">
        <ScrcpyPlayer :key="name" :name="name" :language="language" :show-controls="showControlBar" @size="onStreamSize" />
      </div>
      <img v-else-if="frameUrl" :src="frameUrl" :alt="t('画面预览')" @load="onFrameLoad">
      <div v-else class="preview-empty">{{ t('暂无画面') }}</div>
    </div>
  </article>
  <button v-else class="card preview-strip" type="button" :title="t('画面预览')" @click="expanded = true">
    <svg class="preview-strip-arrow" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
    <span class="preview-strip-text">{{ t('画面预览') }}</span>
  </button>
</template>

<style scoped>
.preview-card { display:flex; flex-direction:column; width:clamp(240px, 22vw, 380px); flex:none; min-height:0; overflow:hidden; }
.preview-head { display:flex; gap:10px; align-items:center; padding:13px 18px; border-bottom:1px solid var(--border); }
.preview-badge { padding:2px 9px; border-radius:7px; font-size:11px; font-weight:700; }
.preview-badge.live { color:var(--green); background:var(--green-soft); }
.preview-badge.stale { color:var(--text-3); background:var(--card-3); }
.preview-icon { display:inline-flex; align-items:center; justify-content:center; padding:2px; border:0; color:var(--text-3); background:transparent; cursor:pointer; }
.preview-icon svg { display:block; width:15px; height:15px; }
.preview-icon:hover { color:var(--text); }
.preview-icon:disabled { opacity:.35; cursor:not-allowed; }
.preview-icon:disabled:hover { color:var(--text-3); }
.preview-icon.control-active { color:var(--accent); }
.preview-frame-wrap { position:relative; overflow:hidden; border-radius:6px; background:#000; }
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
.preview-strip-arrow { width:15px; height:15px; color:var(--text-3); }
.preview-strip:hover .preview-strip-arrow { color:var(--accent); }
.preview-strip-text { color:var(--text-2); font-size:13px; letter-spacing:.15em; writing-mode:vertical-rl; }
@media (max-width:1200px) {
  .preview-card { width:100%; max-height:70vh; }
  .preview-strip { width:100%; height:40px; flex-direction:row; justify-content:center; padding:0 14px; }
  .preview-strip-text { letter-spacing:.08em; writing-mode:horizontal-tb; }
}
</style>
