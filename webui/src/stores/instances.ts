import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import router from '../router'
import type { Instance } from '../types'
import { useToastStore } from './toast'
import { useModalStore } from './modal'

export const useInstancesStore = defineStore('instances', () => {
  const toast = useToastStore()
  const instances = ref<Instance[]>([])
  // 首次加载完成前为 false；总览布局依赖它区分「实例列表未就绪」与「没有实例」
  const loaded = ref(false)
  // Serial execution state from GET /api/serial/state; null when serial is off
  // or the backend is older than this feature.
  const serialState = ref<any>(null)

  // 实例头像由后端托管（Mount /avatars -> assets/gui/avatars），直接按文件名引用。
  function avatarUrl(name?: string) { return name ? `/avatars/${name}` : '' }

  async function loadInstances() {
    try { instances.value = await api.get('/api/instances') } catch (exception: any) { toast.error = exception.message }
    await loadSerial()
    loaded.value = true
  }
  async function loadSerial() {
    try { serialState.value = await api.get('/api/serial/state') } catch { serialState.value = null }
  }

  // An instance is "waiting" when serial is on, it is in the group, alive,
  // not the current holder, and the instance itself reported the waiting
  // state (waiting for the turn, or queued until its next task is due).
  // Backed by /api/serial/state -> instances[name].waiting.
  function serialWaiting(name: string) {
    const serial = serialState.value
    if (!serial?.enable) return false
    const info = serial.instances?.[name]
    if (!info || info.current || !info.alive) return false
    return Boolean(info.waiting)
  }

  // 进程活着但没有到期任务（current_task 为空）时显示"空闲"，出错停止（3）
  // 与正常停止（2）文案同为"已停止"，靠 error 红色样式区分；4 为更新重启。
  function stateText(state?: number, task?: string) {
    if (state === undefined || state === null) return '—'
    if (state === 1) return task ? t('运行中') : t('空闲')
    if (state === 4) return t('更新中…')
    return t('已停止')
  }
  function stateClass(state?: number, task?: string) {
    if (state === 1) return task ? 'running' : 'idle'
    return state === 3 ? 'error' : 'idle'
  }
  // 串行模式下等待中优先于进程状态展示：排队的实例不再是"运行中"
  function displayStatus(name: string, state?: number, task?: string) { return serialWaiting(name) ? t('等待中') : stateText(state, task) }
  function displayStatusClass(name: string, state?: number, task?: string) { return serialWaiting(name) ? 'idle' : stateClass(state, task) }
  function initials(name: string) { return name.slice(0, 1).toUpperCase() }

  const runningCount = computed(() => instances.value.filter(item => item.state === 1 && !serialWaiting(item.name)).length)

  async function lifecycle(action: 'start' | 'stop', name = String(router.currentRoute.value.params.name || '')) {
    try { await api.post(`/api/${name}/${action}`); await loadInstances() } catch (exception: any) {
      if (action === 'start' && exception?.code === 'admin_required') {
        useModalStore().openAlertModal(t('管理员权限不足'), t('PC 客户端需要脚本以管理员权限运行。请退出程序，右键启动程序或快捷方式，在「属性 → 兼容性」中勾选「以管理员身份运行此程序」，然后重新启动。'))
        return
      }
      toast.error = exception.message
    }
  }

  // Manual reorder on the multi-instance page: native HTML5 drag-and-drop
  // driven by a dedicated handle so table inputs keep working.  The new order
  // is persisted to the backend and applies to the dashboard grid and sidebar
  // too, because every view renders the same instances list.
  const dragIndex = ref(-1)
  const dragOverIndex = ref(-1)
  function onDragStart(index: number, event: DragEvent) {
    // The handle itself is the draggable element (draggable lives on it, not on
    // the row): dragstart then bubbles up from the handle, so this guard passes
    // only for handle-initiated drags and text selection inside remark inputs
    // never moves rows.
    const handle = (event.target as HTMLElement).closest('.drag-handle')
    if (!handle) { event.preventDefault(); return }
    dragIndex.value = index
    dragOverIndex.value = -1
    // Show the whole row as the drag image instead of just the handle glyph.
    const row = handle.closest('tr')
    if (row && event.dataTransfer) event.dataTransfer.setDragImage(row, 24, 24)
  }
  function onDragOver(index: number, event: DragEvent) {
    if (dragIndex.value < 0) return
    event.preventDefault()
    if (dragOverIndex.value !== index) dragOverIndex.value = index
  }
  function onDragEnd() { dragIndex.value = -1; dragOverIndex.value = -1 }
  function onDrop() {
    const from = dragIndex.value
    const to = dragOverIndex.value
    dragIndex.value = -1
    dragOverIndex.value = -1
    if (from < 0 || to < 0 || from === to) return
    const list = [...instances.value]
    const [moved] = list.splice(from, 1)
    list.splice(to, 0, moved)
    instances.value = list
    persistOrder()
  }
  async function persistOrder() {
    try { await api.post('/api/instances/order', { names: instances.value.map(item => item.name) }) }
    catch (exception: any) { toast.error = exception.message; await loadInstances() }
  }
  async function saveRemark(instance: Instance, event: Event) {
    const input = event.target as HTMLInputElement
    try {
      const result = await api.post(`/api/${instance.name}/remark`, { remark: input.value.trim() })
      instance.remark = result.remark
      input.value = result.remark
    } catch (exception: any) { toast.error = exception.message; input.value = instance.remark || '' }
  }
  async function importInstance(event: Event) { const file = (event.target as HTMLInputElement).files?.[0]; if (!file) return; try { const response = await fetch('/api/instances/import', { method: 'POST', headers: { 'X-NKAS-Filename': file.name }, body: await file.arrayBuffer() }); const result = await response.json(); if (!response.ok) throw new Error(result.message); await loadInstances() } catch (exception: any) { toast.error = exception.message } }

  return {
    instances, serialState, loaded, loadInstances, loadSerial, serialWaiting,
    stateText, stateClass, displayStatus, displayStatusClass, initials, avatarUrl, runningCount, lifecycle,
    dragIndex, dragOverIndex, onDragStart, onDragOver, onDragEnd, onDrop, saveRemark, importInstance,
  }
})
