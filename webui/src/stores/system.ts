import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t, uiLanguage } from '../i18n'
import router from '../router'
import { useToastStore } from './toast'
import { useAnnouncementsStore } from './announcements'
import { useInstancesStore } from './instances'
import { useWorkspaceStore } from './workspace'

// Cross-store references are resolved lazily inside actions (never at module
// top level) so the store modules may import each other without init cycles.
export const useSystemStore = defineStore('system', () => {
  const toast = useToastStore()
  const systemStatus = ref<any>({ version: '—', updater_state: 'idle', theme: 'light', language: 'zh-CN', home_page: 'overview' })
  const updateInfo = ref<any>({})
  const notices = ref<any[]>([])
  const backendDown = ref(false)

  async function loadSystem() {
    try {
      systemStatus.value = await api.get('/api/system/status')
      uiLanguage.value = systemStatus.value.language
      updateInfo.value = await api.get('/api/system/update')
      const noticeData = await api.get('/api/system/notices')
      notices.value = noticeData.notices || []
      const announcementsStore = useAnnouncementsStore()
      announcementsStore.announcements = noticeData.announcements || []
      // Unread announcements auto-open the center once per session; reconnect
      // reloads of loadSystem must not pop it again.
      if (!announcementsStore.autoShown && announcementsStore.announcements.some(item => !item.read)) {
        announcementsStore.autoShown = true
        announcementsStore.openAnnouncementCenter()
      }
      // Only a valid backend theme may override the local choice: right after
      // an update/reload the status payload can arrive without a usable theme,
      // and falling back to "light" here used to clobber both the page and the
      // stored preference.
      const theme = systemStatus.value.theme
      if (theme === 'dark' || theme === 'light') {
        document.documentElement.dataset.theme = theme
        localStorage.setItem('nkas-theme', theme)
      }
    } catch (exception: any) { toast.error = exception.message }
  }

  function toggleTheme() { const theme = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'; document.documentElement.dataset.theme = theme; localStorage.setItem('nkas-theme', theme); api.post('/api/system/theme', { theme }).then(() => systemStatus.value.theme = theme).catch(exception => toast.error = exception.message) }

  async function setLanguage(language: string) {
    try {
      await api.post('/api/system/language', { language })
      await loadSystem()
      await useWorkspaceStore().loadWorkspace()
    } catch (exception: any) { toast.error = exception.message }
  }

  async function dismissNotice(notice: any) { try { await api.post(`/api/system/notices/${notice.key}/dismiss`); notices.value = notices.value.filter(item => item.key !== notice.key) } catch (exception: any) { toast.error = exception.message } }

  // Backend restart self-healing: the legacy Electron shell never reloads the
  // page, so poll the status endpoint and reload everything once it recovers.
  // The status payload also carries updater_state; surface a toast when an
  // automatic check (startup or scheduled) discovers a new version while the
  // user is somewhere else in the app.
  let lastUpdaterState: any = undefined
  function watchUpdaterState(state: any) {
    const available = state === true || state === 1
    if (available && !(lastUpdaterState === true || lastUpdaterState === 1)) toast.notify(t('发现新版本，可在更新页更新'), 'ok', 0, { label: t('前往更新'), run: () => router.push('/settings') })
    if (updateInfo.value && typeof updateInfo.value === 'object' && 'state' in updateInfo.value) updateInfo.value.state = state
    lastUpdaterState = state
  }
  async function healthCheck() {
    try {
      const status = await api.get('/api/system/status')
      watchUpdaterState(status.updater_state)
      // loadInstances 内含 loadSerial；周期刷新让状态展示（运行中/空闲）拿到
      // 最新的 current_task
      const instancesStore = useInstancesStore()
      await instancesStore.loadInstances()
      if (backendDown.value) {
        backendDown.value = false
        const workspace = useWorkspaceStore()
        workspace.workspaceName = ''
        workspace.socketsName = ''
        await loadSystem(); await instancesStore.loadInstances(); await workspace.loadWorkspace(); workspace.startStateSocket(); workspace.startSockets()
      }
    } catch {
      backendDown.value = true
    }
  }

  // Legacy toast parity: sha + commit count headline, then up to 5 messages
  // trimmed to 54 chars, one per line.
  function autoUpdateTitle(data: any) {
    const sha = String(data?.to_sha || '').trim() || '-'
    const messages = Array.isArray(data?.messages) ? data.messages : []
    const count = Number(data?.commit_count) > 0 ? Number(data.commit_count) : messages.length
    if (systemStatus.value.language === 'en-US') return `Auto-updated to ${sha} (${count} commits)`
    if (systemStatus.value.language === 'ja-JP') return `自動更新完了: ${sha}（${count}件）`
    return `已自动更新到 ${sha}（${count} 条提交）`
  }
  function autoUpdatePreview(data: any) {
    if (!Array.isArray(data?.messages)) return []
    return data.messages.map((msg: any) => String(msg).trim()).filter(Boolean).slice(0, 5).map((msg: string) => msg.length > 54 ? `${msg.slice(0, 51)}...` : msg)
  }

  return { systemStatus, updateInfo, notices, backendDown, loadSystem, toggleTheme, setLanguage, dismissNotice, healthCheck, autoUpdateTitle, autoUpdatePreview }
})
