import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import router from '../router'
import { useToastStore } from './toast'
import { useWorkspaceStore } from './workspace'

// BlaAuth 自动登录：后端无头浏览器跑登录流程，前端弹窗轮询状态；
// 出现滑块验证码时把截图渲染在弹窗里，拖拽事件实时转发给后端驱动页面鼠标。
export const useBlaLoginStore = defineStore('blaLogin', () => {
  const toast = useToastStore()
  const blaLoginOpen = ref(false)
  const blaLoginBusy = ref(false)
  const blaLoginState = ref('')
  const blaShotUrl = ref('')
  let blaLoginTimer: ReturnType<typeof setTimeout> | undefined
  let blaShotTimer: ReturnType<typeof setInterval> | undefined
  let blaDragging = false
  let blaLastMoveSent = 0
  function selectedName() { return String(router.currentRoute.value.params.name || '') }
  function blaStateText() {
    if (blaLoginState.value === 'launching') return t('正在启动浏览器…')
    if (blaLoginState.value === 'logging_in') return t('正在自动填写账号密码…')
    if (blaLoginState.value === 'captcha') return t('请完成滑块验证')
    return t('登录中…')
  }
  function blaLoginStop() {
    if (blaLoginTimer) { clearTimeout(blaLoginTimer); blaLoginTimer = undefined }
    if (blaShotTimer) { clearInterval(blaShotTimer); blaShotTimer = undefined }
    blaLoginBusy.value = false; blaShotUrl.value = ''; blaDragging = false
  }
  function blaFormatExpire(ts: number) {
    const d = new Date(ts * 1000)
    const pad = (n: number) => String(n).padStart(2, '0')
    const off = -d.getTimezoneOffset()
    const sign = off >= 0 ? '+' : '-'
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())} ${sign}${pad(Math.floor(Math.abs(off) / 60))}${pad(Math.abs(off) % 60)}`
  }
  async function pollBlaLogin() {
    let st: any
    try { st = await api.get(`/api/${selectedName()}/bla/login/status`) } catch { blaLoginTimer = setTimeout(pollBlaLogin, 2000); return }
    blaLoginState.value = st.state || ''
    if (st.state === 'captcha') {
      // 验证码图片走独立端点 200ms 直刷，跟手且不占状态轮询
      if (!blaShotTimer) {
        blaShotUrl.value = `/api/${selectedName()}/bla/login/shot?t=${Date.now()}`
        blaShotTimer = setInterval(() => { blaShotUrl.value = `/api/${selectedName()}/bla/login/shot?t=${Date.now()}` }, 200)
      }
      blaLoginTimer = setTimeout(pollBlaLogin, 800)
      return
    }
    if (st.state === 'launching' || st.state === 'logging_in') { blaLoginTimer = setTimeout(pollBlaLogin, 1000); return }
    blaLoginOpen.value = false
    if (st.state === 'success') {
      const result = st.result || {}
      const workspace = useWorkspaceStore()
      const apply = (key: string, value: string) => { const field = workspace.allFields().find(item => item.key === key); if (field && value !== undefined) field.value = value }
      apply('BlaAuth.BlaAuth.Cookie', result.cookie)
      apply('BlaAuth.BlaAuth.XCommonParams', result.xcommonparams)
      apply('BlaAuth.BlaAuth.LoginUser', result.username || '')
      if (result.expire) apply('BlaAuth.BlaAuth.TokenExpire', blaFormatExpire(result.expire))
      blaLoginStop()
      toast.notify(t('登录成功，Cookie 已自动填写'), 'ok', 4000)
      return
    }
    if (st.state === 'cancelled') { blaLoginStop(); return }
    if (st.state === 'timeout') { blaLoginStop(); toast.notify(t('登录超时，请重试'), 'error', 4000); return }
    if (st.state === 'error') { blaLoginStop(); toast.notify(`${t('登录失败')}${st.error ? `：${st.error}` : ''}`, 'error', 6000); return }
    blaLoginStop()
  }
  async function startBlaLogin() {
    if (blaLoginBusy.value) return
    blaLoginOpen.value = true
    blaLoginBusy.value = true
    blaLoginState.value = 'launching'
    try {
      await api.post(`/api/${selectedName()}/bla/login`)
      blaLoginTimer = setTimeout(pollBlaLogin, 800)
    } catch (exception: any) { blaLoginOpen.value = false; blaLoginStop(); toast.error = exception.message }
  }
  async function closeBlaLogin() {
    try { await api.post(`/api/${selectedName()}/bla/login/cancel`) } catch { /* ignore */ }
    blaLoginOpen.value = false
    blaLoginStop()
  }
  function blaSendDrag(phase: string, event: PointerEvent) {
    const img = event.target as HTMLImageElement
    const scale = img.naturalWidth > 0 && img.clientWidth > 0 ? img.naturalWidth / img.clientWidth : 1
    api.post(`/api/${selectedName()}/bla/login/drag`, { phase, x: event.offsetX * scale, y: event.offsetY * scale }).catch(() => {})
  }
  function blaDragStart(event: PointerEvent) {
    event.preventDefault()
    ;(event.target as HTMLImageElement).setPointerCapture(event.pointerId)
    blaDragging = true
    blaLastMoveSent = 0
    blaSendDrag('start', event)
  }
  function blaDragMove(event: PointerEvent) {
    if (!blaDragging) return
    const now = Date.now()
    if (now - blaLastMoveSent < 40) return
    blaLastMoveSent = now
    blaSendDrag('move', event)
  }
  function blaDragEnd(event: PointerEvent) {
    if (!blaDragging) return
    blaDragging = false
    blaSendDrag('end', event)
  }
  return {
    blaLoginOpen, blaLoginBusy, blaLoginState, blaShotUrl,
    blaStateText, startBlaLogin, closeBlaLogin, blaDragStart, blaDragMove, blaDragEnd,
  }
})
