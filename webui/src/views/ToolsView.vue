<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api/client'
import AppSelect from '../components/AppSelect.vue'
import { useRouteInfo } from '../composables/useRouteInfo'
import { t } from '../i18n'
import { useModalStore } from '../stores/modal'
import { useToastStore } from '../stores/toast'
import ConsoleView from './ConsoleView.vue'

const router = useRouter()
const { toolsTab } = useRouteInfo()
const toast = useToastStore()
const { openConfirmModal } = useModalStore()

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
onMounted(loadHosts)
watch(toolsTab, tab => { if (tab === 'hosts') loadHosts() })
</script>

<template>
  <section class="view tools-view" :class="{ 'tools-console': toolsTab === 'console' }">
    <article class="card task-hero">
      <div class="task-icon">🧰</div>
      <div style="flex:1"><h2>{{ t('常用工具') }}</h2></div>
    </article>
    <div class="tools-tabs">
      <button class="tools-tab" :class="{ active: toolsTab === 'hosts' }" @click="router.push('/tools/hosts')">🌐 {{ t('Hosts 修改') }}</button>
      <button class="tools-tab" :class="{ active: toolsTab === 'console' }" @click="router.push('/tools/console')">📟 {{ t('控制台') }}</button>
    </div>
    <article v-if="toolsTab === 'hosts'" class="card group-card">
      <div class="group-head">
        <h4>{{ t('Hosts 修改') }}</h4>
        <span class="hosts-status" :class="{ on: hostsApplied }">{{ hostsApplied ? t('已应用') : t('未应用') }}</span>
      </div>
      <div class="group-body hosts-body">
        <p class="fhelp">{{ t('修改系统 hosts 文件中的 NKAS 段落（仅未注释的行生效），用于改善游戏服务器连接。修改需要管理员权限。') }}</p>
        <div v-if="!hostsSupported" class="hosts-unsupported">⚠ {{ t('当前系统不支持修改 hosts 文件') }}</div>
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
    <ConsoleView v-else embedded />
  </section>
</template>
