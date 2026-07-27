<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{ value: string; picker: any; disabled?: boolean }>()
const emit = defineEmits<{ picked: [value: string]; error: [message: string] }>()
const picking = ref(false)

type PickerReply = { ok?: boolean; canceled?: boolean; path?: string; error?: string }

function legacyPick(payload: any): Promise<PickerReply> {
  if (!window.parent || window.parent === window) return Promise.reject(new Error('文件选择器仅在 Electron 客户端可用，请直接输入路径。'))
  const requestId = `pick-path-${Date.now()}-${Math.random().toString(16).slice(2)}`
  return new Promise((resolve, reject) => {
    const timeout = window.setTimeout(() => finish({ ok: false, error: 'File picker response timed out' }), 15000)
    const onMessage = (event: MessageEvent) => {
      const data = event.data
      if (!data || data.source !== 'nkas-electron' || data.type !== 'dialog:pick-path:response' || data.requestId !== requestId) return
      finish(data.payload || { ok: false, error: 'Invalid file picker response' })
    }
    const finish = (reply: PickerReply) => {
      window.clearTimeout(timeout)
      window.removeEventListener('message', onMessage)
      resolve(reply)
    }
    window.addEventListener('message', onMessage)
    try {
      window.parent.postMessage({ source: 'nkas-webui', type: 'dialog:pick-path:request', requestId, payload }, '*')
    } catch (exception: any) {
      window.clearTimeout(timeout)
      window.removeEventListener('message', onMessage)
      reject(exception)
    }
  })
}

async function pick() {
  if (props.disabled || picking.value) return
  picking.value = true
  const payload = {
    mode: props.picker?.mode === 'directory' ? 'directory' : 'file',
    title: props.picker?.title || '选择文件', defaultPath: props.value || undefined,
    accept: Array.isArray(props.picker?.accept) ? props.picker.accept : [],
  }
  try {
    const nativePicker = (window as any).nkas?.pickPath
    const reply: PickerReply = nativePicker ? await nativePicker(payload) : await legacyPick(payload)
    if (reply.ok && reply.path) emit('picked', reply.path)
    else if (!reply.canceled) emit('error', reply.error || '文件选择失败，请直接输入路径。')
  } catch (exception: any) {
    emit('error', exception?.message || '文件选择失败，请直接输入路径。')
  } finally {
    picking.value = false
  }
}
</script>

<template>
  <button type="button" class="btn sm" :disabled="disabled || picking" @click="pick">{{ picking ? '正在选择…' : (picker?.button_label || '选择文件') }}</button>
</template>
