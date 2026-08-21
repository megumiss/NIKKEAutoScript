import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import type { WebLink } from '../types'
import { useToastStore } from './toast'
import { useSystemStore } from './system'

// 常用链接页：白名单内的站点链接，点击后在界面内 iframe 打开。
// direct=true 的站点允许被 iframe 直接嵌入（无 X-Frame-Options/CSP 限制），
// 直连原站使页面 JS 同域运行、功能完整；direct=false 走后端代理转发。
// 显示名由后端 yaml 下发：i18n 按当前语言覆盖 name，缺省回退到 name 原文。
export const useLinksStore = defineStore('links', () => {
  const toast = useToastStore()
  const webLinks = ref<WebLink[]>([])
  const webUrl = ref('')
  const webLoaded = ref(false)
  const webBusy = ref(false)
  function webFrameSrc(link: WebLink | undefined) { return link?.direct ? link.url : `/api/proxy?url=${encodeURIComponent(link?.url || '')}` }
  function webLink(url: string) { return webLinks.value.find(item => item.url === url) }
  function webLinkName(link: WebLink) { return link.i18n?.[useSystemStore().systemStatus.language] || t(link.name) }
  async function loadWebLinks() {
    try {
      const result = await api.get('/api/proxy/links')
      webLinks.value = result.links || []
      if (!webUrl.value && webLinks.value.length) webUrl.value = webLinks.value[0].url
    } catch (exception: any) { toast.error = exception.message }
    finally { webLoaded.value = true }
  }
  function openWeb(url: string) { if (url !== webUrl.value) { webBusy.value = true; webUrl.value = url } }
  // 刷新当前站点：自增 key 强制 iframe 重挂载，重新加载页面。
  const webFrameKey = ref(0)
  function refreshWeb() { if (!webUrl.value) return; webBusy.value = true; webFrameKey.value++ }
  return { webLinks, webUrl, webLoaded, webBusy, webFrameKey, webFrameSrc, webLink, webLinkName, loadWebLinks, openWeb, refreshWeb }
})
