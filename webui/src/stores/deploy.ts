import { ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import { useToastStore } from './toast'
import { useSystemStore } from './system'
import { useModalStore } from './modal'

// Deploy page: schema-driven editor over config/deploy.yaml.  The backend
// parses groups and per-field comments from deploy/template; edits save per
// field like task settings, and the page carries a warning plus a reset.
export const useDeployStore = defineStore('deploy', () => {
  const toast = useToastStore()
  const deployGroups = ref<any[]>([])
  async function loadDeploy() { try { deployGroups.value = (await api.get('/api/system/deploy')).groups } catch (exception: any) { toast.error = exception.message } }
  async function saveDeployValue(field: any, value: any) {
    if (field.saving || field.entryBusy) return
    field.saving = true
    try {
      const result = await api.patch('/api/system/deploy', { key: field.key, value })
      field.value = result.value
      toast.notify(t('已保存'))
      const system = useSystemStore()
      if (field.key === 'Theme') { document.documentElement.dataset.theme = result.value; localStorage.setItem('nkas-theme', result.value); system.systemStatus.theme = result.value }
      if (field.key === 'HomePage') system.systemStatus.home_page = result.value
      if (field.key === 'Language') await system.setLanguage(result.value)
      // ConsoleEnabled 等开关即时生效：保存后刷新系统状态，侧边栏入口与控制台页即时显隐
      await system.loadSystem()
    } catch (exception: any) { toast.error = exception.message }
    finally { field.saving = false }
  }
  function saveDeployField(field: any, event: Event) {
    const el = event.target as HTMLInputElement
    if (field.key === 'SecurityEntryEnabled') {
      const enabled = el.checked
      // Keep the schema value until the confirmation and write succeed.
      el.checked = field.value === true
      if (!enabled) useModalStore().openConfirmModal('关闭后，任何网络可达的客户端都可直接访问后端。确认关闭安全入口？', () => saveDeployValue(field, false))
      else void saveDeployValue(field, true)
      return
    }
    // 数字框清空时传 null，由后端恢复模板默认值
    saveDeployValue(field, field.widget === 'checkbox' ? el.checked : field.widget === 'number' ? (el.value === '' ? null : Number(el.value)) : el.value)
  }
  function toggleDeployMulti(field: any, value: string) {
    const current = Array.isArray(field.value) ? [...field.value] : []
    const index = current.indexOf(value)
    if (index >= 0) current.splice(index, 1)
    else current.push(value)
    saveDeployValue(field, current)
  }
  return { deployGroups, loadDeploy, saveDeployValue, saveDeployField, toggleDeployMulti }
})
