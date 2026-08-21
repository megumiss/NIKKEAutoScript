import { ref, watch } from 'vue'
import { defineStore } from 'pinia'

export const useToastStore = defineStore('toast', () => {
  const error = ref('')
  const toasts = ref<{ id: number; text: string; kind: string; action?: { label: string; run: () => void } }[]>([])
  let toastSeq = 0
  // duration <= 0 keeps the toast until it is closed manually.
  function notify(text: string, kind = 'ok', duration = 1600, action?: { label: string; run: () => void }) {
    const id = ++toastSeq
    toasts.value.push({ id, text, kind, action })
    if (duration > 0) setTimeout(() => closeToast(id), duration)
  }
  function closeToast(id: number) { toasts.value = toasts.value.filter(toast => toast.id !== id) }
  // Errors are transient toasts, not a persistent topbar strip: every
  // `error = ...` assignment flows through this watcher.
  watch(error, value => { if (value) { notify(value, 'error', 10000); error.value = '' } })
  return { error, toasts, notify, closeToast }
})
