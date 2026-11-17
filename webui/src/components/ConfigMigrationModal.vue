<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { api, expectBackendRestart } from '../api/client'
import FieldPathPicker from './config/FieldPathPicker.vue'
import { t } from '../i18n'
import { useInstancesStore } from '../stores/instances'

interface Preview {
  source: string
  destination: string
  file_count: number
  bytes: number
  conflict_count: number
  conflicts: string[]
  adjusted_files: string[]
  ignored_count: number
  token: string
}
const emit = defineEmits<{ close: [] }>()
const { instances } = storeToRefs(useInstancesStore())
const hasRunning = computed(() => instances.value.some(instance => instance.state === 1))
const source = ref('')
const preview = ref<Preview | null>(null)
const result = ref<{ file_count: number } | null>(null)
const needsRestart = ref(false)
const busy = ref(false)
const restarting = ref(false)
const error = ref('')
const card = ref<HTMLElement | null>(null)
const sourceInput = ref<HTMLInputElement | null>(null)
let previousFocus: HTMLElement | null = null
const size = computed(() => preview.value ? `${(preview.value.bytes / 1024 / 1024).toFixed(2)} MB` : '')
watch(source, () => { preview.value = null; error.value = '' })
onMounted(async () => {
  previousFocus = document.activeElement as HTMLElement
  sourceInput.value?.focus()
  const status = await api.get('/api/system/status').catch(() => null)
  if (status?.config_migration_pending) {
    needsRestart.value = true
    await nextTick()
    card.value?.querySelector<HTMLElement>('.modal-actions .primary')?.focus()
  }
})
onBeforeUnmount(() => previousFocus?.focus())
function close() { if (!busy.value && !restarting.value) emit('close') }
function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.stopPropagation(); close(); return }
  if (event.key !== 'Tab') return
  const controls = Array.from(card.value?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), summary, [tabindex="0"]') || [])
  if (!controls.length) { event.preventDefault(); return }
  const first = controls[0], last = controls[controls.length - 1]
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
}
async function check() {
  if (busy.value || !source.value.trim()) return
  busy.value = true
  preview.value = null
  error.value = ''
  try { preview.value = await api.post('/api/instances/migrate', { action: 'preview', source: source.value.trim() }) }
  catch (exception: any) { error.value = exception.message }
  finally {
    busy.value = false
    await nextTick()
    card.value?.querySelector<HTMLElement>('.modal-actions .primary')?.focus()
  }
}
async function migrate() {
  if (!preview.value || busy.value || hasRunning.value) return
  busy.value = true
  error.value = ''
  try {
    result.value = await api.post('/api/instances/migrate', { action: 'migrate', source: preview.value.source, token: preview.value.token })
  } catch (exception: any) {
    error.value = exception.message
    preview.value = null
    const status = await api.get('/api/system/status').catch(() => null)
    needsRestart.value = status?.config_migration_pending === true
  }
  finally {
    busy.value = false
    await nextTick()
    card.value?.querySelector<HTMLElement>('.modal-actions .primary')?.focus()
  }
}
async function restart() {
  if (restarting.value) return
  restarting.value = true
  error.value = ''
  try {
    expectBackendRestart(true)
    await api.post('/api/restart')
    await new Promise(resolve => setTimeout(resolve, 3000))
    for (let round = 0; round < 60; round++) {
      try {
        // Migrated entry keys can invalidate this browser's cookie after restart.
        const response = await fetch('/api/system/status', { cache: 'no-store' })
        if (response.ok) {
          const status = await response.json()
          if (!status.config_migration_pending) { window.location.reload(); return }
        }
      } catch { /* The backend is temporarily unavailable while restarting. */ }
      await new Promise(resolve => setTimeout(resolve, 2000))
    }
    error.value = t('重启超时，请手动刷新页面')
  } catch (exception: any) { error.value = exception.message }
  finally { expectBackendRestart(false); restarting.value = false }
}
</script>

<template>
  <Teleport to="body">
    <div class="modal-mask" @click.self="close" @keydown="onKeydown">
      <div ref="card" class="modal-card migration-card" role="dialog" aria-modal="true" aria-labelledby="migration-title" :aria-busy="busy || restarting">
        <h3 id="migration-title">{{ t('迁移配置') }}</h3>
        <template v-if="!result && !needsRestart">
          <p class="modal-text">{{ t('从旧部署目录复制 config 和 data 中的配置、账号、实例信息及持久化数据，旧目录保持不变。') }}</p>
          <p class="modal-text">{{ t('不迁移日志、临时文件、虚拟屏运行会话和程序自带资源。请先退出旧部署，并停止当前部署的所有实例。') }}</p>
          <p class="modal-text">{{ t('旧部署内的配置路径会改为当前部署路径，外部游戏和模拟器路径保持不变。加密账号仅能由相同系统用户名读取，跨用户迁移后需重新保存账号。') }}</p>
          <label class="modal-field" for="migration-source">{{ t('旧部署目录') }}
            <span class="migration-path">
              <input id="migration-source" ref="sourceInput" v-model="source" :disabled="busy" :placeholder="t('请输入旧部署根目录的绝对路径')" @keyup.enter="check">
              <FieldPathPicker :value="source" :picker="{ mode: 'directory', title: t('旧部署目录'), button_label: t('选择目录') }" :disabled="busy" @picked="(value: string) => source = value" @error="(message: string) => error = message" />
            </span>
          </label>
          <div v-if="preview" class="migration-preview" aria-live="polite">
            <p>{{ t('当前部署目录') }}: <span class="migration-location">{{ preview.destination }}</span></p>
            <p>{{ t('待迁移文件') }}: {{ preview.file_count }} · {{ size }}</p>
            <p>{{ t('同名覆盖文件') }}: {{ preview.conflict_count }}</p>
            <details v-if="preview.conflicts.length"><summary>{{ t('查看覆盖文件') }}</summary><ul><li v-for="file in preview.conflicts" :key="file">{{ file }}</li></ul></details>
            <details v-if="preview.adjusted_files.length"><summary>{{ t('自动调整的配置文件') }}: {{ preview.adjusted_files.length }}</summary><ul><li v-for="file in preview.adjusted_files" :key="file">{{ file }}</li></ul></details>
            <p class="migration-warning">{{ t('确认迁移将覆盖同名文件，保留当前部署的 Python、Git、ADB 等运行路径、端口和控制台访问设置。安全入口开关、密钥及 Mirror酱 CDK 会一起迁移。完成后需要重启程序。') }}</p>
          </div>
          <p v-if="hasRunning" class="migration-warning">{{ t('请先停止当前部署的所有实例。') }}</p>
        </template>
        <template v-else>
          <p v-if="result" class="modal-text">{{ t('迁移完成') }}: {{ result.file_count }} {{ t('个文件') }}</p>
          <p class="modal-text">{{ t('请立即重启程序以加载迁移后的配置，使用当前部署地址和迁移后的安全入口连接；原浏览器访问凭据可能失效。') }}</p>
        </template>
        <p v-if="error" class="migration-warning" role="alert">{{ error }}</p>
        <div class="modal-actions">
          <button class="btn" :disabled="busy || restarting" @click="close">{{ result || needsRestart ? t('关闭') : t('取消') }}</button>
          <button v-if="result || needsRestart" class="btn primary" :disabled="restarting" @click="restart"><span v-if="restarting" class="btn-spin"></span>{{ restarting ? t('重启中…') : t('立即重启') }}</button>
          <button v-else-if="preview" class="btn primary" :disabled="busy || hasRunning" @click="migrate"><span v-if="busy" class="btn-spin"></span>{{ busy ? t('迁移中…') : t('确认迁移') }}</button>
          <button v-else class="btn primary" :disabled="busy || !source.trim()" @click="check"><span v-if="busy" class="btn-spin"></span>{{ busy ? t('检查中…') : t('预览迁移') }}</button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.migration-card { width:min(600px, calc(100vw - 32px)); max-height:calc(100dvh - 32px); overflow-y:auto; }
.migration-path { display:flex; gap:8px; align-items:center; margin-top:6px; }
.migration-path input { flex:1; min-width:0; margin:0; }
.migration-path .btn { flex:none; height:38px; }
.migration-preview { margin-bottom:16px; padding:12px; border:1px solid var(--border); border-radius:10px; background:var(--card-2); color:var(--text-2); font-size:13px; line-height:1.6; }
.migration-preview p + p { margin-top:6px; }
.migration-preview summary { cursor:pointer; }
.migration-preview ul { max-height:140px; overflow-y:auto; padding-left:20px; overflow-wrap:anywhere; }
.migration-location { overflow-wrap:anywhere; }
.migration-warning { margin:12px 0; color:var(--red); font-size:13px; line-height:1.6; }
</style>
