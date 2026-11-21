import { ref } from 'vue'
import { defineStore } from 'pinia'

export const usePreviewsStore = defineStore('previews', () => {
  const windows = ref<{ name: string; order: number }[]>([])
  let order = 0

  function activate(name: string) {
    const preview = windows.value.find(item => item.name === name)
    if (preview) preview.order = ++order
  }

  function open(name: string) {
    if (!name) return
    if (!windows.value.some(item => item.name === name)) windows.value.push({ name, order: 0 })
    activate(name)
  }

  function close(name: string) {
    windows.value = windows.value.filter(item => item.name !== name)
  }

  return { windows, open, close, activate }
})
