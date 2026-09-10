import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import router from '../router'
import { useToastStore } from './toast'

// 绑定到 Bot：把当前实例已登录的 Cookie / XCommonParams 交给后端转发给
// astrbot_plugin_nikke 的绑定服务，把该账号绑到链接所属 QQ 名下。
// 必须走后端：插件侧对 /api/* 做 Origin 白名单，浏览器直连会被 403。
export const useBlaBindBotStore = defineStore('blaBindBot', () => {
  const toast = useToastStore()
  const bindOpen = ref(false)
  const bindBusy = ref(false)
  const bindLink = ref('')
  function selectedName() { return String(router.currentRoute.value.params.name || '') }
  function openBind() { bindLink.value = ''; bindOpen.value = true }
  function closeBind() { if (!bindBusy.value) bindOpen.value = false }
  async function submitBind() {
    const link = bindLink.value.trim()
    if (!link || bindBusy.value) return
    bindBusy.value = true
    try {
      const result = await api.post(`/api/${selectedName()}/bla/bind`, { link })
      bindOpen.value = false
      bindLink.value = ''
      toast.notify(result.message || t('绑定成功'), 'ok', 4000)
    } catch (exception: any) {
      toast.notify(exception.message || t('绑定失败'), 'error', 6000)
    } finally {
      bindBusy.value = false
    }
  }
  return { bindOpen, bindBusy, bindLink, openBind, closeBind, submitBind }
})
