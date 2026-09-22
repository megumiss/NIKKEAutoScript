<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api } from '../../api/client'
import { t } from '../../i18n'
import AppIcon from '../AppIcon.vue'

const props = defineProps<{ type: string; scope: string; busy: boolean }>()
const status = ref<{ installed: boolean; status: string; version?: string; displays?: unknown[]; message?: string; error?: string } | null>(null)
const loading = ref(false)
const error = ref('')
let requestId = 0

const driverName = computed(() => props.type === 'mttvdd' ? 'MttVDD' : 'ParsecVDD')
const statusText = computed(() => {
  if (loading.value || !status.value && !error.value) return t('查询中…')
  if (error.value) return t('查询失败')
  const data = status.value!
  if (!data.installed) return t('未安装或不可用')
  if (data.error) return `${t('已安装')} · ${t('查询失败')}`
  if (props.type === 'parsecvdd' && data.status.toLowerCase() === 'ok') {
    return `${t('已安装')} · ${data.displays?.length ? t('屏幕已启用') : t('屏幕未启用')}`
  }
  if (data.status === 'enabled' || data.status === 'disabled') {
    return `${t('已安装')} · ${data.status === 'enabled' ? t('已启用') : t('已禁用')}`
  }
  return `${t('已安装')} · ${t('状态未知')}`
})
const statusClass = computed(() => {
  if (loading.value || !status.value && !error.value) return 'idle'
  if (error.value || status.value?.error || !status.value?.installed) return 'error'
  return ['ok', 'enabled'].includes(status.value.status.toLowerCase()) ? 'running' : 'idle'
})
const detail = computed(() => error.value || status.value?.error || status.value?.message || '')

async function refresh() {
  const id = ++requestId
  loading.value = true
  status.value = null
  error.value = ''
  try {
    const result = await api.get(`/api/system/vdd/status?type=${encodeURIComponent(props.type)}`)
    if (id === requestId) status.value = result
  } catch (exception: any) {
    if (id === requestId) error.value = exception.message
  } finally {
    if (id === requestId) loading.value = false
  }
}

watch(() => [props.type, props.scope, props.busy], (_, __, onCleanup) => {
  status.value = null
  error.value = ''
  loading.value = false
  if (!props.busy) refresh()
  // 切换驱动或启停屏幕后，忽略此前尚未返回的状态。
  onCleanup(() => { requestId++ })
}, { immediate: true })
</script>

<template>
  <div class="field">
    <div class="field-label">
      <div class="fname">{{ t('虚拟屏幕驱动状态') }}</div>
      <div class="fhelp">{{ driverName }}<template v-if="status?.version"> · {{ status.version }}</template></div>
      <div v-if="detail" class="fhelp vdd-detail">{{ detail }}</div>
    </div>
    <div class="field-control vdd-status-control">
      <span class="status-pill" :class="statusClass" role="status" aria-live="polite">{{ statusText }}</span>
      <button class="btn" :disabled="loading || busy" @click="refresh"><AppIcon name="refresh" :size="14" /> {{ t('刷新') }}</button>
    </div>
  </div>
</template>

<style scoped>
.vdd-status-control { flex-wrap: wrap; }
.vdd-detail { overflow-wrap: anywhere; }
</style>
