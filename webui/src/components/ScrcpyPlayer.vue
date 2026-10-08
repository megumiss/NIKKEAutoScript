<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ScrcpyMediaStreamPacket } from '@yume-chan/scrcpy'
import type { ScrcpyVideoDecoder } from '@yume-chan/scrcpy-decoder-tinyh264'
import { checkEntry } from '../api/security'
import { useToastStore } from '../stores/toast'
import { useWorkspaceStore } from '../stores/workspace'
import ScrcpyTools from './ScrcpyTools.vue'

const props = defineProps<{ name: string; language: string; showControls: boolean }>()
const workspace = useWorkspaceStore()
const toast = useToastStore()
const emit = defineEmits<{ (e: 'size', size: { width: number; height: number }): void }>()
const labels: Record<string, [string, string, string]> = {
  connecting: ['正在连接设备…', 'Connecting to device…', 'デバイスに接続中…'],
  reconnecting: ['正在应用码率并重连…', 'Applying bitrate and reconnecting…', 'ビットレートを適用して再接続中…'],
  settings_failed: ['码率保存失败，请检查后端日志', 'Could not save the bitrate. Check the backend log.', 'ビットレートを保存できませんでした。バックエンドログを確認してください'],
  connection_failed: ['连接已断开，请重试', 'Disconnected. Please retry.', '接続が切れました。再試行してください'],
  adb_failed: ['ADB 连接失败，请检查设备与 Serial', 'ADB connection failed. Check the device and serial.', 'ADB 接続に失敗しました。デバイスと Serial を確認してください'],
  device_offline: ['设备未在线，请检查 ADB 连接', 'Device offline. Check the ADB connection.', 'デバイスがオフラインです。ADB 接続を確認してください'],
  serial_required: ['检测到多台设备，请在设置中指定 Serial', 'Multiple devices found. Set a serial in settings.', '複数のデバイスがあります。設定で Serial を指定してください'],
  win_platform: ['互动控制仅支持 ADB 设备', 'Interactive control requires an ADB device.', '操作は ADB デバイスのみ対応しています'],
  virtual_display_unavailable: ['虚拟屏幕尚未就绪，请等待任务产生画面后重试', 'Virtual display is not ready. Retry after the task produces a preview.', '仮想画面の準備ができていません。タスクの画面表示後に再試行してください'],
  server_missing: ['投屏组件缺失，请完整更新程序', 'Screen sharing component missing. Update the application.', '画面共有コンポーネントがありません。アプリを更新してください'],
  server_version: ['投屏组件版本不匹配，请完整更新程序', 'Screen sharing version mismatch. Update the application.', '画面共有のバージョンが一致しません。アプリを更新してください'],
  start_failed: ['投屏启动失败，请检查后端日志', 'Screen sharing failed to start. Check the backend log.', '画面共有を開始できませんでした。バックエンドログを確認してください'],
  busy: ['此实例正在其他页面控制中，请先退出', 'This instance is controlled in another page. Close it first.', '別のページで操作中です。先に終了してください'],
  decoder_failed: ['画面解码失败，请重试或降低视频帧率', 'Video decoding failed. Retry or lower the frame rate.', '映像のデコードに失敗しました。再試行するかフレームレートを下げてください'],
  entry: ['请使用最新的完整安全入口重新连接', 'Reconnect using the latest full security entry URL.', '最新のセキュリティ入口 URL で再接続してください'],
  retry: ['重试', 'Retry', '再試行'],
  software: ['使用兼容模式重试', 'Retry in compatibility mode', '互換モードで再試行'],
  screen: ['游戏画面，可点击、拖动或使用键盘操作', 'Game screen. Click, drag or use the keyboard to control.', 'ゲーム画面。クリック、ドラッグ、キーボードで操作できます'],
  back: ['返回', 'Back', '戻る'], home: ['主屏幕', 'Home', 'ホーム'], recent: ['最近应用', 'Recent apps', '最近のアプリ'],
  volume_up: ['音量加', 'Volume up', '音量を上げる'], volume_down: ['音量减', 'Volume down', '音量を下げる'],
  rotate: ['旋转屏幕', 'Rotate screen', '画面を回転'],
  screenshot: ['截屏', 'Screenshot', 'スクリーンショット'],
  screenshot_started: ['已开始下载截屏', 'Screenshot download started', 'スクリーンショットのダウンロードを開始しました'],
  screenshot_failed: ['截屏失败，请重试', 'Could not capture the screen. Please retry.', 'スクリーンショットを取得できませんでした。再試行してください'],
}
function t(key: string) {
  return (labels[key] || labels.connection_failed)[props.language === 'en-US' ? 1 : props.language === 'ja-JP' ? 2 : 0]
}

const host = ref<HTMLElement>()
const status = ref('connecting')
const ready = ref(false)
const capturing = ref(false)
const tools = ref<InstanceType<typeof ScrcpyTools>>()
const bitrate = ref(16000000)
let socket: WebSocket | undefined
let decoder: ScrcpyVideoDecoder | undefined
let disposeSize: { dispose(): void } | undefined
let generation = 0
let watchdog: number | undefined
let frameCheck: number | undefined
let width = 720
let height = 1280
let canvas: HTMLCanvasElement | undefined
const pointers = new Map<number, { x: number; y: number; width: number; height: number }>()

function send(message: object) {
  if (socket?.readyState !== WebSocket.OPEN) return
  if (socket.bufferedAmount > 131072) { fail('connection_failed'); return }
  socket.send(JSON.stringify(message))
}

function releasePointers() {
  const active = [...pointers]
  pointers.clear()
  for (const [pointer, point] of active) send({ type: 'touch', action: 1, pointer, ...point })
}

function stop() {
  generation++
  tools.value?.close()
  releasePointers()
  window.clearTimeout(watchdog)
  window.clearInterval(frameCheck)
  const previous = socket
  socket = undefined
  if (previous) { previous.onclose = null; previous.onmessage = null; previous.close() }
  disposeSize?.dispose()
  disposeSize = undefined
  decoder?.dispose()
  decoder = undefined
  canvas?.remove()
  canvas = undefined
  ready.value = false
  capturing.value = false
}

function fail(code: string) {
  stop()
  status.value = code
}

async function screenshot() {
  if (!ready.value || !canvas || capturing.value) return
  const current = generation
  const filename = `screenshot-${props.name.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').slice(0, 80)}-${new Date().toISOString().replace(/[:.]/g, '-')}.png`
  capturing.value = true
  try {
    // Export the decoded frame at its pixel size, independent of preview scaling.
    const blob = await new Promise<Blob | null>(resolve => canvas!.toBlob(resolve, 'image/png'))
    if (current !== generation || !ready.value) return
    if (!blob) throw new Error('Canvas export failed')
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    try {
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      toast.notify(t('screenshot_started'))
    } finally {
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 30000)
    }
  } catch {
    if (current === generation) toast.notify(t('screenshot_failed'), 'error')
  } finally {
    if (current === generation) capturing.value = false
  }
}

async function connect(software = false) {
  stop()
  status.value = 'connecting'
  const current = generation
  let fallback = software
  let restartRequested = false
  let submitted = 0
  let renderedBase = 0
  let nativeFrames = 0
  let rendered = () => 0
  let writer: ReturnType<ScrcpyVideoDecoder['writable']['getWriter']>
  try {
    await nextTick()
    const { WebCodecsVideoDecoder, BitmapVideoFrameRenderer } = await import('@yume-chan/scrcpy-decoder-webcodecs')
    if (current !== generation || !host.value) return
    canvas = document.createElement('canvas')
    host.value.appendChild(canvas)
    if (!software && WebCodecsVideoDecoder.isSupported) {
      const { ScrcpyVideoCodecId } = await import('@yume-chan/scrcpy')
      if (current !== generation) return
      const renderer = new BitmapVideoFrameRenderer(canvas)
      const draw = renderer.draw.bind(renderer)
      renderer.draw = async frame => { await draw(frame); nativeFrames++ }
      decoder = new WebCodecsVideoDecoder({ codec: ScrcpyVideoCodecId.H264, renderer })
      rendered = () => nativeFrames
    } else {
      const { TinyH264Decoder } = await import('@yume-chan/scrcpy-decoder-tinyh264')
      if (current !== generation) return
      fallback = true
      decoder = new TinyH264Decoder({ canvas })
      const tiny = decoder
      rendered = () => tiny.framesRendered
    }
    const activeDecoder = decoder
    writer = activeDecoder.writable.getWriter()
    writer.closed.catch(() => { if (current === generation) fail('decoder_failed') })
    disposeSize = activeDecoder.sizeChanged(size => {
      width = size.width; height = size.height
      emit('size', size)
    })
    const scheme = location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(`${scheme}://${location.host}/ws/${encodeURIComponent(props.name)}/scrcpy`)
    socket = ws
    ws.binaryType = 'arraybuffer'
    watchdog = window.setTimeout(() => { if (current === generation) fail('connection_failed') }, 60000)
    frameCheck = window.setInterval(() => {
      if (current === generation && activeDecoder.framesRendered > 0) {
        ready.value = true
        status.value = ''
        window.clearTimeout(watchdog)
      }
    }, 100)
    let queue = Promise.resolve()
    ws.onmessage = event => {
      if (current !== generation) return
      if (typeof event.data === 'string') {
        const value = JSON.parse(event.data)
        if (value.type === 'error') { fail(value.code); return }
        if (value.type === 'settings') {
          bitrate.value = value.bitrate
          if (workspace.workspaceName === props.name) {
            const field = workspace.schema.tasks?.Emulator?.groups?.find(group => group.key === 'Scrcpy')
              ?.fields?.find(field => field.arg === 'Bitrate')
            if (field) field.value = value.bitrate
          }
        }
        if (value.type === 'clipboard' || value.type === 'clipboard_ack') tools.value?.handleMessage(value)
        if (value.type === 'restart') {
          restartRequested = true
          releasePointers()
          ready.value = false
          status.value = 'reconnecting'
          tools.value?.close()
          window.clearInterval(frameCheck)
        }
        if (value.type === 'size') {
          releasePointers()
          width = value.width; height = value.height
          emit('size', { width, height })
        }
        return
      }
      queue = queue.then(async () => {
        if (current !== generation) return
        const bytes = new Uint8Array(event.data)
        if (bytes.length < 13) throw new Error('Invalid video packet')
        const view = new DataView(bytes.buffer)
        if (view.getUint32(8) !== bytes.length - 12) throw new Error('Invalid video length')
        const configuration = !!(bytes[0] & 0x40)
        const packet: ScrcpyMediaStreamPacket = configuration
          ? { type: 'configuration', data: bytes.subarray(12) }
          : { type: 'data', keyframe: !!(bytes[0] & 0x20), pts: view.getBigUint64(0) & ((1n << 61n) - 1n), data: bytes.subarray(12) }
        if (configuration) {
          submitted = 0
          renderedBase = rendered() + activeDecoder.framesSkipped
        } else {
          // Do not queue unlimited work in the native decoder or WASM worker.
          const deadline = Date.now() + 10000
          while (submitted - (rendered() + activeDecoder.framesSkipped - renderedBase) >= 4) {
            await new Promise(resolve => window.setTimeout(resolve, 16))
            if (current !== generation) return
            if (Date.now() > deadline) throw new Error('Decoder stalled')
          }
          submitted++
        }
        await writer.write(packet)
        if (current === generation) send({ type: 'ack' })
      }).catch(() => {
        if (current !== generation) return
        fail('decoder_failed')
      })
    }
    ws.onclose = async () => {
      if (current !== generation) return
      if (restartRequested) { await connect(fallback); return }
      const authorized = await checkEntry().catch(() => true)
      if (current === generation) fail(authorized ? 'connection_failed' : 'entry')
    }
  } catch {
    if (current !== generation) return
    if (!fallback) { await connect(true); return }
    fail('decoder_failed')
  }
}

function point(event: PointerEvent) {
  const rect = host.value!.getBoundingClientRect()
  return {
    x: Math.min(width - 1, Math.max(0, Math.floor((event.clientX - rect.left) * width / rect.width))),
    y: Math.min(height - 1, Math.max(0, Math.floor((event.clientY - rect.top) * height / rect.height))), width, height,
  }
}
function pointerDown(event: PointerEvent) {
  if (!ready.value || event.button !== 0) return
  event.preventDefault()
  host.value?.focus({ preventScroll: true })
  host.value?.setPointerCapture(event.pointerId)
  const position = point(event)
  pointers.set(event.pointerId, position)
  send({ type: 'touch', action: 0, pointer: event.pointerId, ...position })
}
function pointerMove(event: PointerEvent) {
  if (!pointers.has(event.pointerId)) return
  const position = point(event)
  pointers.set(event.pointerId, position)
  send({ type: 'touch', action: 2, pointer: event.pointerId, ...position })
}
function pointerUp(event: PointerEvent) {
  const position = pointers.get(event.pointerId)
  if (!position) return
  send({ type: 'touch', action: 1, pointer: event.pointerId, ...position })
  pointers.delete(event.pointerId)
  if (host.value?.hasPointerCapture(event.pointerId)) host.value.releasePointerCapture(event.pointerId)
}
const keys: Record<string, number> = { Escape: 4, Home: 3, Enter: 66, Backspace: 67, Delete: 112, Tab: 61, ArrowUp: 19, ArrowDown: 20, ArrowLeft: 21, ArrowRight: 22, ' ': 62 }
function keydown(event: KeyboardEvent) {
  if (!ready.value || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.key === 'Tab') return
  if (keys[event.key] !== undefined) { event.preventDefault(); send({ type: 'key', key: keys[event.key] }) }
  else if (event.key.length === 1) { event.preventDefault(); send({ type: 'text', text: event.key }) }
}
function paste(event: ClipboardEvent) {
  if (!ready.value) return
  const text = event.clipboardData?.getData('text/plain')
  if (text && new TextEncoder().encode(text).length <= 65536) { event.preventDefault(); send({ type: 'paste', text }) }
}
function visibility() { if (document.hidden) releasePointers() }
onMounted(() => {
  connect()
  window.addEventListener('blur', releasePointers)
  document.addEventListener('visibilitychange', visibility)
})
onBeforeUnmount(() => {
  stop()
  window.removeEventListener('blur', releasePointers)
  document.removeEventListener('visibilitychange', visibility)
})
</script>

<template>
  <div class="scrcpy-player">
    <div ref="host" class="scrcpy-screen" tabindex="0" role="application" :aria-label="t('screen')"
      @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp" @pointercancel="pointerUp"
      @lostpointercapture="pointerUp" @blur="releasePointers" @keydown="keydown" @paste="paste" @contextmenu.prevent>
    </div>
    <div v-if="status" class="scrcpy-state" role="status" aria-live="polite">
      <span>{{ t(status) }}</span>
      <button v-if="status !== 'connecting' && status !== 'reconnecting'" type="button" @click="connect()">{{ t('retry') }}</button>
      <button v-if="status === 'decoder_failed'" type="button" @click="connect(true)">{{ t('software') }}</button>
    </div>
    <div v-if="showControls" class="scrcpy-controls">
      <button v-for="item in [{ label: 'back', key: 4, icon: 'M15 18l-6-6 6-6' }, { label: 'home', key: 3, icon: 'M3 10l9-7 9 7v10H3z' }, { label: 'recent', key: 187, icon: 'M5 5h14v14H5z' }, { label: 'volume_up', key: 24, icon: 'M11 5 6 9H3v6h3l5 4zM16 12h6M19 9v6' }, { label: 'volume_down', key: 25, icon: 'M11 5 6 9H3v6h3l5 4zM16 12h6' }]"
        :key="item.label" type="button" :title="t(item.label)" :aria-label="t(item.label)" :disabled="!ready" @click="send({ type: 'key', key: item.key })">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="item.icon" /></svg>
      </button>
      <button type="button" :title="t('rotate')" :aria-label="t('rotate')" :disabled="!ready" @click="send({ type: 'rotate' })">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5" /></svg>
      </button>
      <button type="button" :title="t('screenshot')" :aria-label="t('screenshot')" :disabled="!ready || capturing" :aria-busy="capturing" @click="screenshot">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4h6l2 3h4v13H3V7h4z" /><circle cx="12" cy="13" r="4" /></svg>
      </button>
      <ScrcpyTools ref="tools" :language="language" :ready="ready" :bitrate="bitrate" @send="send" />
    </div>
  </div>
</template>

<style scoped>
.scrcpy-player { position:relative; display:flex; width:100%; height:100%; background:var(--log-bg); }
.scrcpy-screen { flex:1; min-width:0; height:100%; touch-action:none; outline-offset:-3px; }
.scrcpy-screen:focus-visible { outline:2px solid var(--accent); }
.scrcpy-screen :deep(canvas) { display:block; width:100%; height:100%; }
.scrcpy-state { position:absolute; inset:0; display:flex; flex-direction:column; align-items:center; justify-content:center; gap:12px; padding:16px; background:var(--log-bg); color:var(--text-2); text-align:center; font-size:13px; overflow:auto; }
.scrcpy-state button { border:1px solid var(--border); border-radius:6px; padding:7px 14px; background:var(--card-3); color:var(--text); cursor:pointer; }
.scrcpy-controls { flex:0 0 44px; display:flex; flex-direction:column; align-items:center; gap:4px; overflow:auto; padding:4px 0; }
.scrcpy-controls :deep(button) { flex:none; display:grid; place-items:center; width:36px; height:36px; border:0; border-radius:6px; background:transparent; color:var(--text-2); cursor:pointer; }
.scrcpy-controls :deep(button:hover) { background:var(--card-3); color:var(--accent); }
.scrcpy-controls :deep(button:disabled) { opacity:.4; cursor:default; }
.scrcpy-controls :deep(svg) { width:18px; height:18px; fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }
</style>
