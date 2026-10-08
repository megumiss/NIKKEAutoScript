import { computed, onScopeDispose, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import { useToastStore } from './toast'

type PendingRequest = {
  request_id: string
  instance: string
  uid: string
  username: string
  created_at: string
}

export const useCookieSyncStore = defineStore('cookieSync', () => {
  const toast = useToastStore()
  const pending = ref<PendingRequest | null>(null)
  const busy = ref(false)
  let timer: number | undefined

  const visible = computed(() => Boolean(pending.value))

  async function poll() {
    try {
      const result = await api.get('/api/cookie-sync/pending')
      const next = result.pending?.[0] || null
      if (!next || !pending.value || pending.value.request_id === next.request_id) pending.value = next
    } catch {
      // The regular health check and security-entry flow report connection errors.
    }
  }

  function start() {
    if (timer !== undefined) return
    poll()
    timer = window.setInterval(poll, 2000)
  }

  function stop() {
    if (timer !== undefined) window.clearInterval(timer)
    timer = undefined
  }

  async function confirm() {
    if (!pending.value || busy.value) return
    busy.value = true
    const requestId = pending.value.request_id
    try {
      const result = await api.post(`/api/cookie-sync/${requestId}/confirm`)
      pending.value = null
      toast.notify(result.message || t('Cookie 同步成功'), 'ok', 4000)
    } catch (exception: any) {
      toast.error = exception.message || t('Cookie 同步失败')
    } finally { busy.value = false }
  }

  async function reject() {
    if (!pending.value || busy.value) return
    busy.value = true
    const requestId = pending.value.request_id
    try {
      await api.post(`/api/cookie-sync/${requestId}/reject`)
      pending.value = null
      toast.notify(t('已拒绝 Cookie 同步'), 'ok', 3000)
    } catch (exception: any) {
      toast.error = exception.message || t('操作失败')
    } finally { busy.value = false }
  }

  onScopeDispose(stop)
  return { pending, visible, busy, start, stop, confirm, reject }
})
