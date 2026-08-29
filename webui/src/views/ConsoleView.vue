<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { JsonSocket } from '../api/ws'
import AppIcon from '../components/AppIcon.vue'
import { t } from '../i18n'
import { useSystemStore } from '../stores/system'

// embedded：作为常用工具页的标签页嵌入时隐藏头部卡片，避免与标签栏重复
const props = defineProps<{ embedded?: boolean }>()
const embedded = computed(() => Boolean(props.embedded))

const { systemStatus } = storeToRefs(useSystemStore())
const enabled = computed(() => Boolean(systemStatus.value.console_enabled))

// kind 仅用于着色：echo 本地回显、err  stderr/错误消息，均按纯文本渲染，禁止 v-html
interface ConsoleLine { kind: 'out' | 'err' | 'echo'; text: string }
const lines = ref<ConsoleLine[]>([])
const command = ref('')
const busy = ref(false)
const disconnected = ref(false)

// 常用命令只填入输入框不直接执行；内容为字面量，不进 i18n
const chips = ['adb devices', 'adb connect 127.0.0.1:5555', 'adb disconnect', 'adb tcpip 5555', 'adb kill-server', 'adb shell wm size', 'adb shell wm density', 'python -V', 'git log -3 --oneline']

const HISTORY_KEY = 'nkas-console-history'
const history = ref<string[]>(JSON.parse(sessionStorage.getItem(HISTORY_KEY) || '[]'))
let historyIndex = -1

let socket: JsonSocket | undefined
function connect() {
  socket?.close()
  disconnected.value = false
  // 不自动重连：4403 拒绝后重连只会刷屏，断开提示由用户自行刷新
  socket = new JsonSocket('/ws/console', event => {
    if (event.type === 'output') lines.value.push({ kind: event.stream === 'stderr' ? 'err' : 'out', text: String(event.data ?? '') })
    else if (event.type === 'exit') { busy.value = false; lines.value.push({ kind: 'echo', text: `[${t('退出码')} ${event.code}]` }) }
    else if (event.type === 'error') { busy.value = false; lines.value.push({ kind: 'err', text: String(event.message ?? '') }) }
  }, false, () => { disconnected.value = true; busy.value = false })
  socket.connect()
}
// 部署页保存开关后 loadSystem() 会更新 console_enabled，这里即时跟随
watch(enabled, value => { if (value) connect(); else socket?.close() }, { immediate: true })
onBeforeUnmount(() => socket?.close())

function run() {
  const cmd = command.value.trim()
  if (!cmd || busy.value || disconnected.value) return
  lines.value.push({ kind: 'echo', text: `> ${cmd}` })
  busy.value = true
  socket?.send({ type: 'start', command: cmd })
  if (history.value[history.value.length - 1] !== cmd) {
    history.value.push(cmd)
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(history.value.slice(-50)))
  }
  historyIndex = -1
  command.value = ''
}
function stop() { socket?.send({ type: 'stop' }) }
function clearLines() { lines.value = [] }
function fill(cmd: string) { command.value = cmd }
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter') {
    // 输入法组词中的 Enter 是选字，不能触发执行
    if (!event.isComposing) { event.preventDefault(); run() }
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    if (!history.value.length) return
    historyIndex = historyIndex < 0 ? history.value.length - 1 : Math.max(0, historyIndex - 1)
    command.value = history.value[historyIndex]
  } else if (event.key === 'ArrowDown') {
    event.preventDefault()
    if (historyIndex < 0) return
    historyIndex += 1
    if (historyIndex >= history.value.length) { historyIndex = -1; command.value = '' }
    else command.value = history.value[historyIndex]
  }
}

// 新输出滚到底部（仿 LogsView 的滚动逻辑）
const consoleBody = ref<HTMLElement>()
watch(() => lines.value.length, async () => {
  await nextTick()
  if (consoleBody.value) consoleBody.value.scrollTop = consoleBody.value.scrollHeight
})
</script>

<template>
  <section class="view console-view" :class="{ 'console-embedded': embedded }">
    <article v-if="!enabled" class="card console-disabled"><AppIcon name="terminal-square" :size="16" /> {{ t('控制台未启用，请到部署页开启') }}</article>
    <template v-else>
      <article v-if="!embedded" class="card task-hero">
        <div class="task-icon"><AppIcon name="terminal-square" :size="22" /></div>
        <div style="flex:1"><h2>{{ t('控制台') }}</h2><div class="sub">{{ t('在本机执行命令并实时查看输出，命令在脚本所在目录运行。') }}</div></div>
        <button class="btn" @click="clearLines"><AppIcon name="broom" :size="16" /> {{ t('清屏') }}</button>
      </article>
      <article class="card log-card console-card">
        <div class="console-hint"><AppIcon name="lightbulb" :size="16" /> {{ t('使用完毕后请及时到部署页关闭控制台') }}<button v-if="embedded" class="btn sm console-clear" @click="clearLines"><AppIcon name="broom" :size="14" /> {{ t('清屏') }}</button></div>
        <div v-if="disconnected" class="console-banner"><AppIcon name="alert-triangle" :size="16" /> {{ t('连接已断开（仅本机可用）') }}</div>
        <div ref="consoleBody" class="log-body">
          <div v-if="!lines.length" class="logs-empty">{{ t('暂无输出') }}</div>
          <div v-for="(line, index) in lines" :key="index" class="log-line plain" :class="`console-line-${line.kind}`">
            <span class="log-message">{{ line.text }}</span>
          </div>
        </div>
        <div class="console-chips">
          <button v-for="chip in chips" :key="chip" class="btn sm" @click="fill(chip)">{{ chip }}</button>
        </div>
        <div class="console-input-row">
          <input v-model="command" :placeholder="t('输入命令，Enter 执行，↑/↓ 浏览历史')" :disabled="disconnected" @keydown="onKeydown">
          <span v-if="busy" class="console-status">{{ t('命令执行中…') }}</span>
          <button v-if="busy" class="btn danger" @click="stop">{{ t('停止') }}</button>
          <button v-else class="btn primary" :disabled="!command.trim() || disconnected" @click="run">{{ t('执行') }}</button>
        </div>
      </article>
    </template>
  </section>
</template>
