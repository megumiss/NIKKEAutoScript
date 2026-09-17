<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api/client'
import { useModalStore } from '../stores/modal'
import { useToastStore } from '../stores/toast'

const props = defineProps<{ disabled?: boolean }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
const entryPath = ref('')
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const reveal = ref(false)
const entryUrl = computed(() => entryPath.value ? new URL(entryPath.value, location.origin).href : '')
const toast = useToastStore()
const modal = useModalStore()

function accept(result: any) {
  entryPath.value = result.security_entry.entry_path || ''
  reveal.value = false
}
async function load() {
  loading.value = true; error.value = ''; emit('busy', true)
  try { accept(await api.get('/api/security/entry')) }
  catch (exception: any) { error.value = exception.message }
  finally { loading.value = false; emit('busy', false) }
}
onMounted(load)
function regenerate() {
  if (busy.value || loading.value || props.disabled) return
  modal.openConfirmModal('重新生成后，旧入口、旧浏览器凭据及 WebSocket 将失效。当前页面会保留访问权限，远程 App 需要填写新入口；自动化任务不受影响。', async () => {
    busy.value = true; error.value = ''; emit('busy', true)
    try { accept(await api.post('/api/security/entry/regenerate')); toast.notify('已重新生成，请复制新的安全入口') }
    catch (exception: any) { error.value = exception.message }
    finally { busy.value = false; emit('busy', false) }
  })
}
async function copy() {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(entryUrl.value)
    else {
      const input = document.createElement('textarea')
      input.value = entryUrl.value; input.style.position = 'fixed'; input.style.opacity = '0'
      document.body.appendChild(input); input.select()
      const copied = document.execCommand('copy'); input.remove()
      if (!copied) throw new Error('当前浏览器不支持复制，请显示入口后手动复制。')
    }
    toast.notify('完整安全入口已复制，请勿公开分享')
  } catch (exception: any) { error.value = exception.message }
}
</script>

<template>
  <div class="field field-wide" :aria-busy="loading || busy">
    <div class="field-label">
      <label class="fname" for="security-entry-url">完整安全入口</label>
      <div class="fhelp">浏览器和远程 App 通用。远程访问时替换服务器 IP / 域名，保留完整 /entry/ 路径。入口等同凭据，请勿公开，公网建议使用 HTTPS。</div>
    </div>
    <div class="field-control">
      <div v-if="entryUrl" class="entry-controls">
        <input id="security-entry-url" :type="reveal ? 'text' : 'password'" :value="entryUrl" readonly autocomplete="off" spellcheck="false">
        <button class="btn" :aria-pressed="reveal" @click="reveal = !reveal">{{ reveal ? '隐藏' : '显示' }}</button>
        <button class="btn primary" :disabled="busy || disabled" @click="copy">复制入口</button>
        <button class="btn danger" :disabled="busy || disabled" @click="regenerate">重新生成入口</button>
      </div>
      <div v-if="loading" class="sub" role="status">正在读取安全入口…</div>
      <div v-if="error" class="entry-error" role="alert">{{ error }} <button class="btn" :disabled="loading || busy || disabled" @click="load">重试</button></div>
    </div>
  </div>
</template>

<style scoped>
.entry-controls { display: flex; gap: 10px; flex-wrap: wrap; }
.entry-controls input { flex: 1 1 240px; min-width: 0; }
.entry-error { margin-top: 8px; color: var(--red); }
</style>
