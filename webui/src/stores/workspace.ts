import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { JsonSocket } from '../api/ws'
import { t } from '../i18n'
import router from '../router'
import type { Field, LogEntry } from '../types'
import { useToastStore } from './toast'
import { useInstancesStore } from './instances'

export const useWorkspaceStore = defineStore('workspace', () => {
  const toast = useToastStore()
  const schema = ref<any>({ menus: [], tasks: {} })
  const queue = ref<any>({ running: [], pending: [], waiting: [] })
  const schemaReady = ref(false)
  const collapsed = ref<Record<string, boolean>>({})
  const railCollapsed = ref<Record<string, boolean>>({})
  const taskFilter = ref('')
  const activeGroup = ref('')
  const importBusy = ref<Record<string, boolean>>({})
  const notifyTestBusy = ref(false)

  const selectedName = computed(() => String(router.currentRoute.value.params.name || ''))
  const selectedTask = computed(() => String(router.currentRoute.value.params.task || ''))
  const taskSchema = computed(() => schema.value.tasks[selectedTask.value])

  // Names of the instance whose schema and per-instance sockets are currently
  // loaded.  Switching tasks within one instance must not refetch the schema
  // or rebuild the rail, so both are keyed by instance name.
  const workspaceName = ref('')
  const socketsName = ref('')

  // Entries carry a stable id so the v-for patch is append/remove-only instead
  // of re-rendering every row on each incoming line, and a precomputed rank so
  // the level filter does not re-run a regex over the whole buffer per line.
  const logs = ref<LogEntry[]>([])
  let logSeq = 0
  // Retention is per level class so the scrollback length stays fixed: with
  // the backend at DEBUG level a debug flood would otherwise push visible
  // lines out of a shared raw buffer and shrink the scrollable range.  Debug
  // lines and non-debug lines each keep the last LOG_CLASS_LIMIT entries, so
  // every filter level renders a stable number of rows.
  const LOG_CLASS_LIMIT = 400
  const LOG_BUFFER_LIMIT = LOG_CLASS_LIMIT * 2
  // Incremented on every batch so auto-scroll still triggers once the buffer
  // length saturates at LOG_BUFFER_LIMIT.
  const logTick = ref(0)
  const LOG_LEVEL_PATTERN = /log-line lv-(debug|info|warn|err)/
  const autoScroll = ref(true)
  const logLevel = ref('info')
  const LOG_LEVEL_RANK: Record<string, number> = { debug: 0, info: 1, warn: 2, err: 3 }
  function pushLogs(payload: string | string[]) {
    // The replay arrives as one batched array; live lines arrive one at a time.
    for (const html of Array.isArray(payload) ? payload : [payload]) {
      const match = html.match(LOG_LEVEL_PATTERN)
      logs.value.push({ id: ++logSeq, html, rank: match ? LOG_LEVEL_RANK[match[1]] : -1 })
    }
    if (logs.value.length > LOG_BUFFER_LIMIT) {
      let debugCount = 0
      for (const entry of logs.value) if (entry.rank === 0) debugCount++
      // Evict the oldest entry of whichever class is over its limit.
      while (logs.value.length > LOG_BUFFER_LIMIT) {
        const evictDebug = debugCount > LOG_CLASS_LIMIT
        const index = logs.value.findIndex(entry => (entry.rank === 0) === evictDebug)
        if (index < 0) break
        logs.value.splice(index, 1)
        if (evictDebug) debugCount--
      }
    }
    logTick.value++
  }
  // Level rows carry the lv-* class rendered by the backend log template.
  // Section dividers have no level and stay visible at every filter setting;
  // traceback blocks share their error summary's rank and are filtered with it.
  const visibleLogs = computed(() => {
    const threshold = LOG_LEVEL_RANK[logLevel.value] ?? 0
    return logs.value.filter(line => line.rank < 0 || line.rank >= threshold).slice(-LOG_CLASS_LIMIT)
  })

  const visibleMenus = computed(() => {
    const q = taskFilter.value.trim().toLowerCase()
    return schema.value.menus.map((menu: any) => {
      const tasks = menu.tasks.map((task: any) => {
        const nameMatched = !q || task.name.toLowerCase().includes(q)
        let matchedFields: any[] = []
        if (!nameMatched) {
          const page = menu.page === 'tool' ? 'tool' : 'task'
          for (const group of schema.value.tasks[task.key]?.groups || []) {
            for (const field of group.fields || []) {
              if ((field.title || '').toLowerCase().includes(q) || (field.help || '').toLowerCase().includes(q)) {
                matchedFields.push({ ...field, groupKey: group.key, page })
              }
            }
          }
        }
        if (!nameMatched && !matchedFields.length) return null
        return { ...task, nameMatched, matchedFields }
      }).filter(Boolean)
      return { ...menu, tasks }
    }).filter((menu: any) => menu.tasks.length)
  })

  function taskEnabled(task: string) { return schema.value.tasks[task]?.groups?.some((group: any) => group.fields.some((field: Field) => field.key.endsWith('.Scheduler.Enable') && field.value)) }
  function allFields() { return Object.values(schema.value.tasks).flatMap((task: any) => task.groups.flatMap((group: any) => group.fields)) as Field[] }
  function isWideField(field: Field) { return Boolean(field.path_picker) || ['item_table', 'interception_stone_charts', 'interception_stone_import', 'textarea', 'priority'].includes(field.widget) }

  async function refreshSpecial(field: Field) { if (field.data_endpoint && field.widget !== 'interception_stone_import') field.special_data = await api.get(field.data_endpoint) }
  async function loadWorkspace() {
    if (!selectedName.value) return
    schemaReady.value = false
    try {
      schema.value = await api.get(`/api/${selectedName.value}/schema`)
      const monitors = await api.get('/api/system/monitors').catch(() => [])
      allFields().forEach(field => { if (field.key.endsWith('.ScreenNumber')) field.options = monitors })
      collapsed.value = {}
      Object.values(schema.value.tasks).forEach((task: any) => task.groups.forEach((group: any) => { if (group.collapsed) collapsed.value[group.key] = true }))
      // Rail groups start collapsed; in-app navigation keeps them untouched
      // because loadWorkspace only runs on an instance switch.
      railCollapsed.value = {}
      schema.value.menus.forEach((menu: any) => { railCollapsed.value[menu.key] = true })
      await Promise.all(allFields().filter(field => field.data_endpoint && field.widget !== 'interception_stone_import').map(field => refreshSpecial(field).catch(() => null)))
      queue.value = await api.get(`/api/${selectedName.value}/queue`)
      schemaReady.value = true
      workspaceName.value = selectedName.value
      activeGroup.value = taskSchema.value?.groups?.[0]?.key || ''
      toast.error = ''
    } catch (exception: any) { toast.error = exception.message }
  }

  let stateSocket: JsonSocket | undefined
  let logSocket: JsonSocket | undefined
  let queueSocket: JsonSocket | undefined
  function startStateSocket() {
    stateSocket?.close()
    const instancesStore = useInstancesStore()
    stateSocket = new JsonSocket('/ws/state', event => { const instance = instancesStore.instances.find(item => item.name === event.name); if (instance) instance.state = event.state })
    stateSocket.connect()
  }
  function startSockets() {
    // Log and queue sockets are per instance; keep them alive while moving
    // between pages of the same instance so logs keep collecting and the
    // replay is not duplicated on return.
    if (!selectedName.value || socketsName.value === selectedName.value) return
    logSocket?.close(); queueSocket?.close()
    socketsName.value = selectedName.value
    logSocket = new JsonSocket(`/ws/${selectedName.value}/log`, event => pushLogs(event.html))
    queueSocket = new JsonSocket(`/ws/${selectedName.value}/queue`, event => queue.value = event)
    logSocket.connect(); queueSocket.connect()
  }
  function closeSockets() {
    stateSocket?.close()
    logSocket?.close()
    queueSocket?.close()
  }

  function openQueueItem(item: any) {
    const menu = schema.value.menus.find((item2: any) => item2.tasks.some((task: any) => task.key === item.command))
    router.push(`/i/${selectedName.value}/${menu?.page === 'tool' ? 'tool' : 'task'}/${item.command}`)
  }

  async function saveValue(field: Field, value: any) {
    try {
      const result = await api.patch(`/api/${selectedName.value}/config`, { key: field.key, value })
      if (!result.ok) throw new Error(result.message)
      field.value = result.applied[field.key]
      toast.notify(t('已保存'))
    } catch (exception: any) {
      toast.error = exception.message
      throw exception
    }
  }
  function save(field: Field, event: Event) {
    const input = event.target as HTMLInputElement
    const value = field.widget === 'checkbox' ? input.checked : input.value
    // Roll the control back to the last persisted value when the save fails.
    saveValue(field, value)
      .then(() => {
        // Manually typed launcher paths autofill the game path too, matching
        // the file-dialog picker behavior.
        if (field.path_picker?.after_select === 'autofill_game_path_from_launcher' && field.widget !== 'checkbox') {
          autofillGamePathFromLauncher(input.value)
        }
      })
      .catch(() => {
        if (field.widget === 'checkbox') input.checked = Boolean(field.value)
        else input.value = field.value ?? ''
      })
  }
  // datetime-local only renders the "T" separator; stored values may use a
  // space ("1989-12-27 00:00:00"), which would otherwise render as an empty
  // picker.  Normalize for display only; seconds are dropped as the control
  // does not edit them.
  function datetimeValue(value: any) { return String(value ?? '').replace(' ', 'T').slice(0, 16) }
  const DATETIME_SAVE_DELAY = 1000
  const datetimeSaveTimers = new Map<string, number>()
  function cancelDatetimeSave(field: Field) {
    const timer = datetimeSaveTimers.get(field.key)
    if (timer !== undefined) window.clearTimeout(timer)
    datetimeSaveTimers.delete(field.key)
  }
  function commitDatetime(field: Field, input: HTMLInputElement) {
    cancelDatetimeSave(field)
    // Native datetime inputs report an incomplete value as empty. Do not write
    // the stored value back while the user is moving between date/time parts.
    if (!input.value || input.value === datetimeValue(field.value)) return
    saveValue(field, input.value).catch(() => { input.value = datetimeValue(field.value) })
  }
  function scheduleDatetimeSave(field: Field, event: Event) {
    const input = event.target as HTMLInputElement
    cancelDatetimeSave(field)
    if (!input.value) return
    datetimeSaveTimers.set(field.key, window.setTimeout(() => commitDatetime(field, input), DATETIME_SAVE_DELAY))
  }
  function flushDatetimeSave(field: Field, event: Event) {
    const input = event.target as HTMLInputElement
    if (!input.value) {
      cancelDatetimeSave(field)
      input.value = datetimeValue(field.value)
      return
    }
    commitDatetime(field, input)
  }
  // Clearing saves immediately so the backend restores the default.
  function clearField(field: Field) {
    cancelDatetimeSave(field)
    saveValue(field, '').catch(() => {})
  }
  function clearDatetimeSaveTimers() {
    datetimeSaveTimers.forEach(timer => window.clearTimeout(timer))
    datetimeSaveTimers.clear()
  }
  // Resolve `..` segments so the auto-filled path has no `..` (e.g. D:\a\..\b -> D:\b).
  function normalizePath(path: string): string {
    const separator = path.includes('\\') ? '\\' : '/'
    const prefix = /^[A-Za-z]:[\\/]/.test(path) ? path.slice(0, 3) : path.startsWith('\\\\') ? '\\\\' : ''
    const stack: string[] = []
    for (const part of path.slice(prefix.length).split(/[\\/]+/)) {
      if (!part || part === '.') continue
      if (part === '..') stack.pop()
      else stack.push(part)
    }
    return prefix + stack.join(separator)
  }
  async function autofillGamePathFromLauncher(launcherPath: string) {
    // An empty/cleared launcher path cannot derive a game path; do not fill garbage.
    if (!launcherPath) return
    const gamePath = allFields().find(item => item.key === 'PCClient.PCClientInfo.GamePath')
    if (gamePath && !gamePath.value) {
      const separator = launcherPath.includes('\\') ? '\\' : '/'
      await saveValue(gamePath, normalizePath(`${launcherPath.replace(/[\\/][^\\/]+$/, '')}${separator}..${separator}NIKKE${separator}game${separator}nikke.exe`)).catch(() => null)
    }
  }
  async function pickedPath(field: Field, path: string) {
    try { await saveValue(field, path) } catch { return }
    if (field.path_picker?.after_select !== 'autofill_game_path_from_launcher') return
    await autofillGamePathFromLauncher(path)
  }
  async function importInterception(field: Field, path: string) {
    if (!field.data_endpoint) return
    importBusy.value[field.key] = true
    try { const result = await api.post(field.data_endpoint, { path }); if (!result.ok) throw new Error(result.message || t('导入失败')); const chart = allFields().find(item => item.widget === 'interception_stone_charts'); if (chart) await refreshSpecial(chart); toast.notify(`已导入 ${result.imported || 0} 条，跳过 ${result.skipped || 0} 条。`, 'ok', 3000) } catch (exception: any) { toast.error = exception.message } finally { delete importBusy.value[field.key] }
  }
  async function testNotify() {
    if (notifyTestBusy.value) return
    notifyTestBusy.value = true
    try {
      const result = await api.post(`/api/${selectedName.value}/notify/test`)
      const parts: string[] = []
      if (result.windows === true) parts.push(t('系统通知已发送'))
      if (result.windows === false) parts.push(t('系统通知发送失败'))
      if (result.onepush === true) parts.push(t('OnePush 推送成功'))
      if (result.onepush === false) parts.push(t('OnePush 推送失败，请检查配置'))
      toast.notify(parts.join('；'), result.ok ? 'ok' : 'error', 4000)
    } catch (exception: any) { toast.error = exception.message } finally { notifyTestBusy.value = false }
  }
  async function startTool() {
    try { await api.post(`/api/${selectedName.value}/tool/${selectedTask.value}/start`) } catch (exception: any) { toast.error = exception.message }
  }

  return {
    schema, queue, schemaReady, collapsed, railCollapsed, taskFilter, activeGroup, importBusy, notifyTestBusy,
    selectedName, selectedTask, taskSchema, workspaceName, socketsName,
    logs, logTick, autoScroll, logLevel, pushLogs, visibleLogs, visibleMenus,
    taskEnabled, allFields, isWideField, refreshSpecial, loadWorkspace,
    startStateSocket, startSockets, closeSockets, openQueueItem,
    saveValue, save, datetimeValue, cancelDatetimeSave, scheduleDatetimeSave, flushDatetimeSave, clearField, clearDatetimeSaveTimers,
    normalizePath, autofillGamePathFromLauncher, pickedPath, importInterception, testNotify, startTool,
  }
})
