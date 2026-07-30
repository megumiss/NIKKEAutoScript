<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../../api/client'

// Browsers cannot return an arbitrary full filesystem path, so the local
// backend opens the host-native dialog for every desktop shell.
const props = defineProps<{ value: string; picker: any; disabled?: boolean }>()
const emit = defineEmits<{ picked: [value: string]; error: [message: string] }>()
const picking = ref(false)

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
    const reply = await api.post('/api/system/pick-path', payload)
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
