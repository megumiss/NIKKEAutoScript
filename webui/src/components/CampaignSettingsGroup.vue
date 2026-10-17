<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { api } from '../api/client'
import { t } from '../i18n'

// 推图任务共用的设置组：与任务配置组同构，资源同步只是其中一个字段，后续推图相关的配置都加在这里。
type Resource = {
  state: string; syncing: boolean; condition: 'missing' | 'latest' | 'outdated' | 'unknown'
  repository: string; error: string; log: string[]; git: boolean
  local: { exists: boolean; version: string | null; sha: string | null; synced_at: string | null; components: Record<string, { chapters?: number; version?: string }> }
  remote: { sha?: string | null; branch?: string | null; version?: string | null; error?: string | null }
}
const resource = ref<Resource | null>(null)
const error = ref('')
const collapsed = ref(false)
let timer: number | undefined
let autoSyncTried = false

// 打开页面时先检查资源目录与版本；缺失或过期自动同步一次，之后只响应手动同步。
async function load(check = true) {
  window.clearTimeout(timer)
  try {
    const result: Resource = await api.get(check ? '/api/resource' : '/api/resource?check=0')
    resource.value = result
    error.value = ''
    if (result.syncing) { timer = window.setTimeout(() => load(false), 2000); return }
    if (!check) { timer = window.setTimeout(() => load(true), 300); return }
    if (!autoSyncTried && (result.condition === 'missing' || result.condition === 'outdated')) { autoSyncTried = true; await sync() }
  } catch (reason) { error.value = String(reason) }
}
async function sync() {
  try { await api.post('/api/resource/sync'); error.value = '' } catch (reason) { error.value = String(reason); return }
  timer = window.setTimeout(() => load(false), 1500)
}
const summary = computed(() => {
  const value = resource.value
  if (!value) return t('正在检查资源…')
  if (!value.local.exists) return t('尚未下载资源')
  const chapters = value.local.components?.maps?.chapters
  const parts = [chapters ? `${t('地图')} ${chapters} ${t('章')}` : t('地图'), `${t('版本')} ${value.local.version ?? '—'}`]
  if (value.local.sha) parts.push(value.local.sha.slice(0, 8))
  if (value.local.synced_at) parts.push(`${t('同步于')} ${value.local.synced_at}`)
  return parts.join(' · ')
})
const stateText = computed(() => {
  const value = resource.value
  if (!value) return ''
  if (value.syncing) return value.log.length ? value.log[value.log.length - 1] : t('正在同步资源…')
  if (value.state === 'failed') return t('同步失败')
  if (value.condition === 'missing') return t('未安装资源')
  if (value.condition === 'outdated') return t('有新版本')
  if (value.condition === 'latest') return t('已是最新')
  return value.remote?.error ? t('无法检查更新') : t('版本未知')
})
const stateClass = computed(() => [resource.value?.condition, { syncing: resource.value?.syncing, failed: resource.value?.state === 'failed' }])
const message = computed(() => error.value || resource.value?.error || resource.value?.remote?.error || '')
load()
onBeforeUnmount(() => window.clearTimeout(timer))
</script>

<template>
  <article id="group-CampaignSettings" class="card group-card campaign-settings" :class="{ collapsed }">
    <button class="group-head" @click="collapsed = !collapsed">
      <div class="group-title">
        <h4>{{ t('推图设置') }}</h4>
        <div class="group-help">{{ t('自动主线与收集品任务共用的设置。章节地图等数据来自独立的资源仓库，按需同步，不随 NKAS 更新。') }}</div>
      </div>
      <span class="group-summary">›</span>
    </button>
    <div class="group-body">
      <div class="field">
        <div class="field-label">
          <div class="fname">{{ t('地图资源') }}<span v-if="stateText" class="resource-state" :class="stateClass">{{ stateText }}</span></div>
          <div class="fhelp">{{ summary }}</div>
        </div>
        <div class="field-control resource-control">
          <button class="btn primary" :disabled="!resource || resource.syncing" @click="sync">{{ resource?.syncing ? t('同步中…') : t('同步资源') }}</button>
        </div>
      </div>
      <p v-if="message" class="resource-error" role="status">{{ message }}</p>
    </div>
  </article>
</template>

<style scoped>
.campaign-settings { margin-bottom:18px; }
.resource-control { display:flex; gap:10px; align-items:center; justify-content:flex-end; flex-wrap:wrap; }
.resource-state { margin-left:8px; padding:3px 8px; border-radius:6px; font-size:12px; font-weight:600; vertical-align:1px; color:var(--accent); background:var(--accent-soft); }
.resource-state.missing,.resource-state.outdated,.resource-state.failed { color:var(--red); background:var(--red-soft); }
.resource-error { margin:0; padding:0 22px 14px; color:var(--red); font-size:12px; }
</style>
