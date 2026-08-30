<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import AppIcon from '../components/AppIcon.vue'
import AppSelect from '../components/AppSelect.vue'
import FieldPathPicker from '../components/config/FieldPathPicker.vue'
import { useRouteInfo } from '../composables/useRouteInfo'
import { t } from '../i18n'
import { useModalStore } from '../stores/modal'
import { useToastStore } from '../stores/toast'
import ConsoleView from './ConsoleView.vue'

const router = useRouter()
const { toolsTab } = useRouteInfo()
const toast = useToastStore()
const { openConfirmModal } = useModalStore()
function onPickError(message: string) { toast.error = message }

interface HostsSection { name: string; lines: string[]; common: boolean; default_on: boolean }

const hostsSupported = ref(true)
const hostsApplied = ref(false)
const hostsContent = ref('')
const hostsBusy = ref(false)
const sections = ref<HostsSection[]>([])
const selectedRegion = ref('')
const regionOptions = computed(() => sections.value.filter(s => !s.common).map(s => ({ value: s.name, label: s.name })))

// 文本框只显示通用段和所选区服的内容（含注释头），其余区服不展示
function compose() {
  hostsContent.value = sections.value
    .filter(section => section.common || section.name === selectedRegion.value)
    .map(section => [`# ${section.name}`, ...section.lines].join('\n'))
    .join('\n')
}

async function loadHosts() {
  try {
    const data = await api.get('/api/tools/hosts')
    hostsSupported.value = Boolean(data.supported)
    hostsApplied.value = Boolean(data.applied)
    sections.value = Array.isArray(data.sections) ? data.sections : []
    const regions = sections.value.filter(s => !s.common)
    const fallback = (regions.find(s => s.default_on) || regions[0])?.name || ''
    selectedRegion.value = fallback
    if (hostsApplied.value) {
      // 按 hosts 文件中生效的记录行反推当前区服
      const active = new Set<string>(data.active || [])
      const found = regions.find(s => s.lines.length && s.lines.every(line => active.has(line)))
      if (found) selectedRegion.value = found.name
    }
    compose()
  } catch (exception: any) { toast.error = exception.message }
}
function onRegionChange(value: any) { selectedRegion.value = String(value); compose() }
async function applyHosts() {
  if (hostsBusy.value) return
  if (!hostsContent.value.trim()) { toast.notify(t('Hosts 内容不能为空'), 'error', 3000); return }
  hostsBusy.value = true
  try {
    const data = await api.post('/api/tools/hosts', { action: 'add', hosts: hostsContent.value })
    hostsApplied.value = Boolean(data.applied)
    // 所有行均为注释时后端不会写入，applied 保持 false
    if (hostsApplied.value) toast.notify(t('已写入 hosts 文件'))
    else toast.notify(t('没有生效：所有行均为注释或为空'), 'error', 4000)
  } catch (exception: any) { toast.error = exception.message } finally { hostsBusy.value = false }
}
function revertHosts() {
  openConfirmModal(t('将删除 hosts 文件中 NKAS 写入的解析记录，恢复默认 DNS 解析。'), async () => {
    hostsBusy.value = true
    try {
      await api.post('/api/tools/hosts', { action: 'delete' })
      hostsApplied.value = false
      compose()
      toast.notify(t('已还原'))
    } catch (exception: any) { toast.error = exception.message } finally { hostsBusy.value = false }
  })
}

// ---- 游戏多开 ----
const cloneSource = ref('')
const cloneTarget = ref('')
const cloneSuffix = ref('')
const cloneList = ref<string[]>([])
const cloneJob = ref<any>({ running: false, step: '', total: 0, copied: 0, error: '', result: null })
let cloneTimer: number | undefined

function formatSize(bytes: number) {
  if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(1)} GB`
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`
  return `${Math.ceil(bytes / 1024)} KB`
}
const cloneProgress = computed(() => cloneJob.value.total ? Math.min(100, Math.floor(cloneJob.value.copied / cloneJob.value.total * 100)) : 0)

function stopClonePolling() { if (cloneTimer) { clearInterval(cloneTimer); cloneTimer = undefined } }
function startClonePolling() {
  stopClonePolling()
  cloneTimer = window.setInterval(loadCloneInfo, 1500)
}
async function loadCloneInfo() {
  try {
    const data = await api.get('/api/tools/game_clone')
    cloneList.value = data.clones || []
    if (!cloneSuffix.value) cloneSuffix.value = String(data.next_suffix || '')
    cloneJob.value = data.job || cloneJob.value
    if (cloneJob.value.running) startClonePolling()
    else {
      stopClonePolling()
      if (cloneJob.value.error) toast.notify(cloneJob.value.error, 'error', 6000)
      else if (cloneJob.value.result) toast.notify(t('复制完成'), 'ok', 4000)
    }
  } catch (exception: any) { stopClonePolling(); toast.error = exception.message }
}
async function startClone() {
  if (cloneJob.value.running) return
  try {
    cloneJob.value = { ...cloneJob.value, error: '', result: null }
    await api.post('/api/tools/game_clone', { source: cloneSource.value, target: cloneTarget.value, suffix: cloneSuffix.value })
    startClonePolling()
  } catch (exception: any) { toast.error = exception.message }
}

onMounted(() => { loadHosts(); loadCloneInfo() })
onBeforeUnmount(stopClonePolling)
watch(toolsTab, tab => {
  if (tab === 'hosts') loadHosts()
  if (tab === 'clone') loadCloneInfo()
})
</script>

<template>
  <section class="view tools-view" :class="{ 'tools-console': toolsTab === 'console' }">
    <article class="card task-hero">
      <div class="task-icon"><AppIcon name="designtools" :size="22" /></div>
      <div style="flex:1"><h2>{{ t('常用工具') }}</h2></div>
    </article>
    <div class="tools-tabs">
      <button class="tools-tab" :class="{ active: toolsTab === 'hosts' }" @click="router.push('/tools/hosts')"><AppIcon name="globe" :size="16" /> {{ t('Hosts 修改') }}</button>
      <button class="tools-tab" :class="{ active: toolsTab === 'clone' }" @click="router.push('/tools/clone')"><AppIcon name="gamepad" :size="16" /> {{ t('游戏多开') }}</button>
      <button class="tools-tab" :class="{ active: toolsTab === 'console' }" @click="router.push('/tools/console')"><AppIcon name="terminal-square" :size="16" /> {{ t('控制台') }}</button>
    </div>
    <article v-if="toolsTab === 'hosts'" class="card group-card">
      <div class="group-head">
        <h4>{{ t('Hosts 修改') }}</h4>
        <span class="hosts-status" :class="{ on: hostsApplied }">{{ hostsApplied ? t('已应用') : t('未应用') }}</span>
      </div>
      <div class="group-body hosts-body">
        <p class="fhelp">{{ t('修改系统 hosts 文件中的 NKAS 段落（仅未注释的行生效），用于改善游戏服务器连接。修改需要管理员权限。') }}</p>
        <div v-if="!hostsSupported" class="hosts-unsupported"><AppIcon name="alert-triangle" :size="14" /> {{ t('当前系统不支持修改 hosts 文件') }}</div>
        <div v-if="regionOptions.length" class="hosts-region">
          <span class="hosts-region-label">{{ t('区服') }}</span>
          <AppSelect :model-value="selectedRegion" :options="regionOptions" @change="onRegionChange" />
        </div>
        <textarea v-model="hostsContent" class="textarea-mono hosts-editor" spellcheck="false" :disabled="!hostsSupported"></textarea>
        <div class="hosts-actions">
          <button class="btn danger" :disabled="hostsBusy || !hostsApplied" @click="revertHosts">{{ t('还原') }}</button>
          <button class="btn primary" :disabled="hostsBusy || !hostsSupported" @click="applyHosts">{{ t('应用') }}</button>
        </div>
      </div>
    </article>
    <article v-else-if="toolsTab === 'clone'" class="card group-card">
      <div class="group-head"><h4>{{ t('游戏多开') }}</h4></div>
      <div class="group-body hosts-body">
        <p class="fhelp">{{ t('复制一份游戏安装目录，重命名新副本的启动器与游戏程序，并写入新副本的路径配置。复制前请关闭游戏和启动器。') }}</p>
        <div class="clone-field">
          <span class="hosts-region-label">{{ t('游戏安装目录') }}</span>
          <input v-model="cloneSource" class="clone-input" spellcheck="false" :disabled="cloneJob.running">
          <FieldPathPicker :value="cloneSource" :picker="{ mode: 'directory', button_label: t('选择目录') }" :disabled="cloneJob.running" @picked="(v: string) => cloneSource = v" @error="onPickError" />
        </div>
        <div class="clone-field">
          <span class="hosts-region-label">{{ t('副本安装目录') }}</span>
          <input v-model="cloneTarget" class="clone-input" spellcheck="false" :disabled="cloneJob.running">
          <FieldPathPicker :value="cloneTarget" :picker="{ mode: 'directory', button_label: t('选择目录') }" :disabled="cloneJob.running" @picked="(v: string) => cloneTarget = v" @error="onPickError" />
        </div>
        <div class="clone-field">
          <span class="hosts-region-label">{{ t('副本编号') }}</span>
          <input v-model="cloneSuffix" class="clone-input clone-suffix" type="number" min="2" :disabled="cloneJob.running">
        </div>
        <div v-if="cloneList.length" class="clone-existing">
          <span class="hosts-region-label">{{ t('已有配置') }}</span>
          <span v-for="name in cloneList" :key="name" class="clone-chip">{{ name }}</span>
        </div>
        <div v-if="cloneJob.running" class="clone-progress">
          <div class="clone-progress-bar"><div class="clone-progress-fill" :style="{ width: `${cloneProgress}%` }"></div></div>
          <span class="clone-progress-text">{{ t(cloneJob.step || '准备') }} {{ formatSize(cloneJob.copied) }} / {{ formatSize(cloneJob.total) }} ({{ cloneProgress }}%)</span>
        </div>
        <div v-if="cloneJob.error" class="hosts-unsupported"><AppIcon name="alert-triangle" :size="14" /> {{ cloneJob.error }}</div>
        <div v-if="cloneJob.result" class="clone-result">
          <div>{{ t('启动器') }}: {{ cloneJob.result.launcher }}</div>
          <div>{{ t('游戏程序') }}: {{ cloneJob.result.game }}</div>
        </div>
        <div class="hosts-actions">
          <button class="btn primary" :disabled="cloneJob.running" @click="startClone">{{ cloneJob.running ? t('复制中…') : t('开始复制') }}</button>
        </div>
      </div>
    </article>
    <ConsoleView v-else embedded />
  </section>
</template>
