import { onBeforeUnmount, watch } from 'vue'
import { useWorkspaceStore } from '../stores/workspace'

export function useEventStageRefresh() {
  const workspace = useWorkspaceStore()
  let timer: number | undefined
  let revision = 0

  function active() {
    return workspace.schemaReady && workspace.workspaceName === workspace.selectedName
      && ['Event', 'Event2'].includes(workspace.selectedTask) && !document.hidden
  }

  function scheduleRefresh(delay: number, version: number) {
    timer = window.setTimeout(() => { void refresh(version) }, Math.min(delay, 2_147_483_647))
  }

  async function refresh(version: number) {
    if (version !== revision || !active()) return
    try {
      const result = await workspace.refreshEventInfo()
      if (version !== revision || !active() || !result) return
      const next = result.events[workspace.selectedTask]?.next_change_at
      if (next) {
        const delay = Date.parse(next) - Date.parse(result.server_time)
        // A small margin avoids repeatedly fetching just before the boundary.
        if (Number.isFinite(delay)) scheduleRefresh(Math.max(1000, delay + 1000), version)
      }
    } catch {
      if (version === revision && active()) scheduleRefresh(30_000, version)
    }
  }

  function restart() {
    window.clearTimeout(timer)
    revision++
    if (active()) void refresh(revision)
  }

  watch(() => [workspace.schemaReady, workspace.workspaceName, workspace.selectedName, workspace.selectedTask, workspace.eventInfoVersion], restart, { immediate: true })
  document.addEventListener('visibilitychange', restart)
  window.addEventListener('focus', restart)
  onBeforeUnmount(() => {
    revision++
    window.clearTimeout(timer)
    document.removeEventListener('visibilitychange', restart)
    window.removeEventListener('focus', restart)
  })
}
