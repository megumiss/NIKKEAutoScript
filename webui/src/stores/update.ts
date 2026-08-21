import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import router from '../router'
import { useToastStore } from './toast'
import { useSystemStore } from './system'
import { useModalStore } from './modal'

// 更新页：源码更新、桌面启动器（nkas.exe）自更新与强制重启。
// App 的路由 watch 在进入 /settings 时调用 refresh*，onMounted 调用
// notifyDesktopUpdate，因此这些状态放在 store 而不是视图组件里。
export const useUpdateStore = defineStore('update', () => {
  const toast = useToastStore()
  const updateChecking = ref(false)
  // The update state changes behind the page's back (startup check, scheduled
  // daily check), so refresh it whenever the update view is opened and keep
  // polling while a check runs instead of showing a stale 检查中 forever.
  let updatePollTimer = 0
  function isSettings() { return router.currentRoute.value.path === '/settings' }
  async function refreshUpdateInfo() {
    window.clearTimeout(updatePollTimer)
    const system = useSystemStore()
    try { system.updateInfo = await api.get('/api/system/update') } catch { return }
    if (isSettings() && system.updateInfo.state === 'checking') updatePollTimer = window.setTimeout(refreshUpdateInfo, 3000)
  }
  // Desktop launcher (nkas.exe) self-update: the Tauri shell exposes the
  // updater through its own commands instead of the backend API, so this flow
  // mirrors the source update card but drives window.__TAURI__.core.invoke.
  // In plain browsers the bridge is absent and the card stays disabled.
  const desktopInvoke = (): any => (window as any).__TAURI__?.core?.invoke
  const isDesktopShell = Boolean(desktopInvoke())
  const desktopUpdate = ref<any>(null)
  const desktopChecking = ref(false)
  const desktopApplying = ref(false)
  let desktopUpdatePollTimer = 0
  async function refreshDesktopUpdate() {
    window.clearTimeout(desktopUpdatePollTimer)
    if (!isDesktopShell) return
    try { desktopUpdate.value = await desktopInvoke()('desktop_update_status') } catch { return }
    if (isSettings() && desktopUpdate.value?.checking) desktopUpdatePollTimer = window.setTimeout(refreshDesktopUpdate, 3000)
  }
  async function checkDesktopUpdate() {
    if (desktopChecking.value || !isDesktopShell) return
    desktopChecking.value = true
    try {
      desktopUpdate.value = await desktopInvoke()('desktop_update_check')
      // Poll until the background check finishes so the button can switch to
      // the update action when a new version shows up.
      for (let round = 0; round < 30; round++) {
        await new Promise(resolve => setTimeout(resolve, 2000))
        desktopUpdate.value = await desktopInvoke()('desktop_update_status')
        if (!desktopUpdate.value?.checking) break
      }
    } catch (exception: any) { toast.error = String(exception?.message || exception) } finally { desktopChecking.value = false }
  }
  async function applyDesktopUpdate() {
    if (desktopApplying.value || !isDesktopShell) return
    useModalStore().openConfirmModal(t('更新启动器将中断正在运行的任务并自动重启程序，确定继续？'), async () => {
      desktopApplying.value = true
      // A successful apply exits and relaunches the shell, so the invoke may
      // never resolve; a rejection carries the failure reason as a string.
      let failed = false
      desktopInvoke()('desktop_update_apply').catch((exception: any) => {
        failed = true
        desktopApplying.value = false
        toast.error = String(exception?.message || exception)
      })
      // The download runs while the old backend is still answering, so a live
      // status endpoint does not mean the update finished.  Wait for the
      // backend to go down first (the shell exited for replacement)…
      let shellExited = false
      for (let round = 0; round < 60 && !failed; round++) {
        await new Promise(resolve => setTimeout(resolve, 3000))
        try { await api.get('/api/system/status') } catch { shellExited = true; break }
      }
      if (failed) return
      if (!shellExited) {
        desktopApplying.value = false
        toast.error = t('更新超时，请手动刷新页面')
        return
      }
      // …then wait for the new process to serve again and reload for a fresh
      // state.  The success toast is shown after the reload via a flag.
      sessionStorage.setItem('nkas-desktop-updated', '1')
      for (let round = 0; round < 60; round++) {
        await new Promise(resolve => setTimeout(resolve, 3000))
        try { await api.get('/api/system/status'); window.location.reload(); return } catch { }
      }
      sessionStorage.removeItem('nkas-desktop-updated')
      desktopApplying.value = false
      toast.error = t('重启超时，请手动刷新页面')
    })
  }
  // The launcher checks for its own updates once in the background at startup;
  // surface the same kind of toast as the source updater when it found one.
  async function notifyDesktopUpdate() {
    if (!isDesktopShell) return
    try { desktopUpdate.value = await desktopInvoke()('desktop_update_status') } catch { return }
    if (desktopUpdate.value?.updateAvailable) toast.notify(t('启动器有新版本，可在更新页更新'), 'ok', 0, { label: t('前往更新'), run: () => router.push('/settings') })
  }
  async function checkUpdate() {
    if (updateChecking.value) return
    updateChecking.value = true
    const system = useSystemStore()
    try {
      await api.post('/api/update/check')
      // Poll until the background check finishes so the button can switch to
      // the update action when a new version shows up.
      for (let round = 0; round < 30; round++) {
        await new Promise(resolve => setTimeout(resolve, 2000))
        system.updateInfo = await api.get('/api/system/update')
        if (system.updateInfo.state !== 'checking') break
      }
      // A failed check resolves to state "failed" (with the reason in
      // `error`) instead of the idle 0/false, so surface it as a toast
      // rather than letting the card silently read "up to date".
      if (system.updateInfo.state === 'failed') {
        const reason = String(system.updateInfo.error || '')
        toast.notify(t('检查更新失败') + (reason ? `：${reason}` : ''), 'error', 10000)
      }
    } catch (exception: any) { toast.error = exception.message } finally { updateChecking.value = false }
  }
  const updating = ref(false)
  function runUpdate() {
    if (updating.value) return
    useModalStore().openConfirmModal(t('更新源码将等待当前任务执行完毕后再进行，期间请勿启动新任务，确定继续？'), executeSourceUpdate)
  }
  async function executeSourceUpdate() {
    updating.value = true
    const system = useSystemStore()
    try {
      await api.post('/api/update')
      // The updater first waits for running instances to stop, then pulls and
      // reloads the backend; poll until it leaves the in-progress states.
      let state = ''
      let round = 0
      for (; round < 300; round++) {
        await new Promise(resolve => setTimeout(resolve, 2000))
        // A failed request just means the backend is mid-reload; keep polling.
        try { system.updateInfo = await api.get('/api/system/update') } catch { continue }
        state = String(system.updateInfo.state)
        if (!['checking', 'start', 'wait', 'run update'].includes(state)) break
      }
      if (state === 'failed') {
        toast.error = t('更新失败')
        return
      }
      if (round >= 300) {
        toast.error = t('更新超时，请稍后手动刷新页面')
        return
      }
      // We initiated this update, so any terminal state other than "failed"
      // means the cycle completed.  In particular a backend reload resets the
      // updater state to idle (0/false); if the reload fits between two polls
      // neither "finish"/"reload" nor a failed request is ever observed, and
      // treating only those as success would silently skip the page reload.
      toast.notify(t('更新完成，正在刷新页面…'))
      for (let waitRound = 0; waitRound < 60; waitRound++) {
        try { await api.get('/api/system/status'); break } catch { await new Promise(resolve => setTimeout(resolve, 2000)) }
      }
      window.location.reload()
    } catch (exception: any) { toast.error = exception.message } finally { updating.value = false }
  }
  const restarting = ref(false)
  function forceRestart() {
    if (restarting.value) return
    useModalStore().openConfirmModal(t('强制重启将中断正在运行的任务，确定继续？'), executeForceRestart)
  }
  async function executeForceRestart() {
    restarting.value = true
    try { await api.post('/api/restart') } catch (exception: any) { toast.error = exception.message; restarting.value = false; return }
    // The backend tears itself down right after answering; give it a moment to
    // go down, then poll until it is back and reload for a fresh state.
    toast.notify(t('后端正在重启，页面将自动刷新…'), 'ok', 4000)
    await new Promise(resolve => setTimeout(resolve, 3000))
    for (let round = 0; round < 60; round++) {
      try { await api.get('/api/system/status'); window.location.reload(); return } catch { await new Promise(resolve => setTimeout(resolve, 2000)) }
    }
    toast.error = t('重启超时，请手动刷新页面')
    restarting.value = false
  }
  function clearPollTimers() {
    window.clearTimeout(updatePollTimer)
    window.clearTimeout(desktopUpdatePollTimer)
  }
  return {
    updateChecking, refreshUpdateInfo,
    isDesktopShell, desktopUpdate, desktopChecking, desktopApplying,
    refreshDesktopUpdate, checkDesktopUpdate, applyDesktopUpdate, notifyDesktopUpdate,
    checkUpdate, updating, runUpdate, restarting, forceRestart, clearPollTimers,
  }
})
