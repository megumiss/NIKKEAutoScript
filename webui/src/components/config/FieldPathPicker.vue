<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../../api/client'

// Two picker channels, best experience first:
// 1. Electron shell bridge (postMessage): the shell opens a native dialog
//    parented to its own window, always in front — same smooth feel as the
//    instance-import file dialog.  The bridge exists in every released
//    Electron build.
// 2. Backend dialog (/api/system/pick-path): plain browsers cannot obtain
//    full filesystem paths from a file input, so the local server opens a
//    native dialog on the host instead.
const props = defineProps<{ value: string; picker: any; disabled?: boolean }>()
const emit = defineEmits<{ picked: [value: string]; error: [message: string] }>()
const picking = ref(false)

type PickerReply = { ok?: boolean; canceled?: boolean; path?: string; error?: string }

// Shared by every picker instance: after the first click we know whether the
// bridge answers, and later clicks skip the probing timeout.
let bridgeState: 'unknown' | 'available' | 'unavailable' = 'unknown'

const inIframe = window.parent !== window
// The released Electron shell is a file:// page; document.referrer inside its
// iframe tells us the bridge is there without having to wait for the user to
// close a dialog before we can detect a missing bridge.
const inElectronShell = inIframe && document.referrer.startsWith('file://')

function bridgePick(payload: any): Promise<PickerReply | null> {
  // Resolves to null when no bridge answers in time, so the caller falls
  // back to the backend dialog.
  const requestId = `pick-path-${Date.now()}-${Math.random().toString(16).slice(2)}`
  return new Promise((resolve) => {
    // Inside the released shell the bridge is guaranteed; wait indefinitely
    // because the reply only arrives after the user closes the dialog.  In an
    // unexpected iframe (e.g. a dev shell over http) cap the wait and fall
    // back instead of hanging on 正在选择….
    const timer = inElectronShell ? 0 : window.setTimeout(() => finish(null), 15000)
    const onMessage = (event: MessageEvent) => {
      const data = event.data
      if (!data || data.source !== 'nkas-electron' || data.type !== 'dialog:pick-path:response' || data.requestId !== requestId) return
      finish(data.payload || { ok: false, error: 'Invalid file picker response' })
    }
    const finish = (reply: PickerReply | null) => {
      if (timer) window.clearTimeout(timer)
      window.removeEventListener('message', onMessage)
      resolve(reply)
    }
    window.addEventListener('message', onMessage)
    try {
      window.parent.postMessage({ source: 'nkas-webui', type: 'dialog:pick-path:request', requestId, payload }, '*')
    } catch {
      finish(null)
    }
  })
}

async function pick() {
  if (props.disabled || picking.value) return
  picking.value = true
  const payload = {
    mode: props.picker?.mode === 'directory' ? 'directory' : 'file',
    title: props.picker?.title || '',
    defaultPath: props.value || '',
    accept: Array.isArray(props.picker?.accept) ? props.picker.accept : [],
  }
  try {
    let reply: PickerReply | null = null
    if (inIframe && bridgeState !== 'unavailable') {
      reply = await bridgePick(payload)
      bridgeState = reply ? 'available' : 'unavailable'
    }
    if (!reply) reply = await api.post('/api/system/pick-path', payload)
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
  <button type="button" class="btn" :disabled="disabled || picking" @click="pick">{{ picking ? '正在选择…' : (picker?.button_label || '选择文件') }}</button>
</template>
