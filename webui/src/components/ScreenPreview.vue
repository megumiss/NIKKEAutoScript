<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { api } from '../api/client'

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
  '未配置 ws-scrcpy 地址': { 'en-US': 'ws-scrcpy URL not configured', 'ja-JP': 'ws-scrcpy URL が未設定です' },
  'Serial 为 auto 时无法使用互动模式': { 'en-US': 'Interactive mode requires a fixed serial (not auto)', 'ja-JP': 'Serial が auto の場合は使用できません' },
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

// ws-scrcpy 页面经后端同源代理（/scrcpy/page）下发，因此可以直接读写 iframe 内容。
// 布局方案：向 iframe 注入 CSS 强制视频画布拉伸填满 .video 区域，控制栏用
// display:none 裁掉（三点按钮切换显示，默认收起）；父页面只需读取
// 视频画布的原始像素尺寸（rawW/rawH，因各浏览器 localStorage 里的视频设置而异，
// 不能硬编码），按真实宽高比决定卡片宽度。iframe 始终 100% 填满遮罩，不使用 transform。
const frameEl = ref<HTMLIFrameElement>()
const rawW = ref(256)
const rawH = ref(480)
const barW = ref(52)
const showControlBar = ref(false)
const wrapW = ref(0)
const wrapH = ref(0)

// 注入到 iframe 内的样式：视频画布拉伸填满 .video，控制栏默认隐藏。
// 注意控制栏必须 flex-direction:column：ws-scrcpy 原生靠 float/block 纵向堆叠按钮，
// 只给 display:flex 会变成横向排列，按钮被挤到 52px 宽度里看不见（空白条）。
const IFRAME_CSS = `
html, body { height:100% !important; margin:0 !important; overflow:hidden !important; background:#000 !important; }
.device-view { display:flex !important; width:100% !important; height:100% !important; }
.video { flex:1 1 auto !important; width:auto !important; height:100% !important; position:relative !important; overflow:hidden !important; }
.video canvas { position:absolute !important; inset:0 !important; width:100% !important; height:100% !important; }
.control-buttons-list { display:none !important; }
html.nkas-show-bar .control-buttons-list { display:flex !important; flex-direction:column !important; align-items:center !important; flex:0 0 auto !important; order:2 !important; height:100% !important; }
`

const frameSrc = computed(() => {
  const url = scrcpy.value?.url
  if (!url) return ''
  const hashIndex = url.indexOf('#')
  const hash = hashIndex >= 0 ? url.slice(hashIndex) : ''
  // 经后端同源代理下发（静态资源也走同源转发，避免 wasm 跨域被 CORS 拦截）
  return `/scrcpy/${encodeURIComponent(props.name)}/${hash}`
})

const frameWrapStyle = computed(() => ({
  width: `${wrapW.value}px`,
  height: `${wrapH.value}px`,
}))

function updateBarVisibility() {
  const doc = frameEl.value?.contentDocument
  if (!doc) return
  doc.documentElement.classList.toggle('nkas-show-bar', showControlBar.value)
  const bar = doc.querySelector('.control-buttons-list') as HTMLElement | null
  if (!bar) return
  // 内联 !important 双保险：即使注入样式表被覆盖/丢失也能生效
  if (showControlBar.value) {
    bar.style.setProperty('display', 'flex', 'important')
    bar.style.setProperty('flex-direction', 'column', 'important')
    bar.style.setProperty('align-items', 'center', 'important')
    if (bar.offsetWidth > 0) barW.value = bar.offsetWidth
  } else {
    bar.style.setProperty('display', 'none', 'important')
  }
}

function syncFrame() {
  const doc = frameEl.value?.contentDocument
  if (!doc) return
  // 注入布局样式（幂等）
  if (!doc.getElementById('nkas-embed-style')) {
    const style = doc.createElement('style')
    style.id = 'nkas-embed-style'
    style.textContent = IFRAME_CSS
    doc.head.appendChild(style)
  }
  updateBarVisibility()
  // 读取视频画布原始像素尺寸（流启动后才可知）
  const canvas = doc.querySelector('canvas.video-layer') as HTMLCanvasElement | null
  if (canvas && canvas.width > 0 && canvas.height > 0) {
    rawW.value = canvas.width
    rawH.value = canvas.height
  }
  measure()
}

function onProxyFrameLoad() {
  // 流启动是异步的，首帧到达后画布尺寸才确定，加载后多同步几次收敛
  syncFrame()
  window.setTimeout(syncFrame, 1500)
  window.setTimeout(syncFrame, 4000)
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
  const extraW = showControlBar.value ? barW.value : 0
  if (window.innerWidth <= 1200) {
    cardWidth.value = 0
    wrapW.value = Math.round(body.clientWidth - BODY_PADDING_X)
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

// Interactive mode swaps the JPEG frame for the external ws-scrcpy stream.
// The availability info is per instance and loaded lazily on first expand.
type ScrcpyInfo = { available: boolean; url?: string; reason?: string }
const scrcpy = ref<ScrcpyInfo | null>(null)
const interactive = ref(false)

const controlTitle = computed(() => {
  if (!scrcpy.value) return t('控制')
  if (scrcpy.value.available) return interactive.value ? t('退出控制') : t('控制')
  const reasons: Record<string, string> = {
    not_configured: '未配置 ws-scrcpy 地址',
    serial_auto: 'Serial 为 auto 时无法使用互动模式',
    win_platform: '仅 adb 可用',
  }
  return t(reasons[scrcpy.value.reason || ''] || '未配置 ws-scrcpy 地址')
})

async function loadScrcpy() {
  try {
    scrcpy.value = await api.get(`/api/${encodeURIComponent(props.name)}/scrcpy`)
  } catch {
    scrcpy.value = { available: false, reason: 'not_configured' }
  }
}

function toggleInteractive() {
  if (!scrcpy.value?.available) return
  interactive.value = !interactive.value
  // 进入互动模式时控制栏默认收起，可用头部按钮展开
  if (interactive.value) showControlBar.value = false
  // The iframe covers the frame area; no point polling JPEG frames meanwhile.
  if (interactive.value) stopPolling()
  else startPolling()
  measure()
}

function toggleControlBar() {
  showControlBar.value = !showControlBar.value
  updateBarVisibility()
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

async function openPreview() {
  startPolling()
  if (!scrcpy.value) loadScrcpy()
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
  scrcpy.value = null
  if (expanded.value) {
    startPolling()
    loadScrcpy()
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
        <button class="preview-icon" :class="{ 'control-active': interactive }" type="button" :disabled="scrcpy !== null && !scrcpy.available" :title="controlTitle" @click="toggleInteractive">
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
      <div v-if="interactive && frameSrc" class="preview-frame-wrap" :style="frameWrapStyle">
        <iframe ref="frameEl" class="preview-frame" :src="frameSrc" :title="t('画面预览')"
          allow="autoplay; clipboard-read; clipboard-write" @load="onProxyFrameLoad"></iframe>
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
.preview-frame { position:absolute; top:0; left:0; width:100%; height:100%; border:0; }
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
