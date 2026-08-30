import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import router from '../router'
import { useToastStore } from './toast'
import { useInstancesStore } from './instances'
import { useUiStore } from './ui'

export const useModalStore = defineStore('modal', () => {
  const toast = useToastStore()
  const modal = ref<{ type: '' | 'create' | 'rename' | 'delete' | 'resetDeploy' | 'confirm' | 'alert' | 'avatar'; name: string; renameTarget: string; origin: string; template: string; avatar: string; avatarTarget: string; busy: boolean }>({ type: '', name: '', renameTarget: '', origin: 'template-nkas', template: 'intl', avatar: '', avatarTarget: '', busy: false })
  const originOptions = computed(() => ['template-nkas', ...useInstancesStore().instances.map(item => item.name)])
  const deployTemplateOptions = computed(() => [{ value: 'intl', label: t('国际') }, { value: 'cn', label: t('大陆') }, { value: 'docker-intl', label: t('Docker国际') }, { value: 'docker-cn', label: t('Docker国内') }])
  // Pre-fill the create form with the first free nkas-style name (nkas, nkas2,
  // nkas3…) so the modal needs no placeholder hint.
  function defaultInstanceName() {
    const names = new Set(useInstancesStore().instances.map(item => item.name))
    if (!names.has('nkas')) return 'nkas'
    let index = 2
    while (names.has(`nkas${index}`)) index++
    return `nkas${index}`
  }
  function openCreateModal() { modal.value = { type: 'create', name: defaultInstanceName(), renameTarget: '', origin: useInstancesStore().instances[0]?.name || 'template-nkas', template: 'intl', avatar: '', avatarTarget: '', busy: false } }
  function openRenameModal(name: string) { modal.value = { type: 'rename', name, renameTarget: name, origin: '', template: '', avatar: '', avatarTarget: '', busy: false } }
  function openDeleteModal(name: string) { modal.value = { type: 'delete', name, renameTarget: '', origin: '', template: '', avatar: '', avatarTarget: '', busy: false } }
  function openResetDeployModal() { modal.value = { type: 'resetDeploy', name: '', renameTarget: '', origin: '', template: 'intl', avatar: '', avatarTarget: '', busy: false } }
  function openAvatarModal(name: string) {
    const instance = useInstancesStore().instances.find(item => item.name === name)
    modal.value = { type: 'avatar', name, renameTarget: '', origin: '', template: '', avatar: instance?.avatar ?? '', avatarTarget: name, busy: false }
  }
  // Generic confirmation so every risky action shares the same modal instead of
  // mixing native confirm() with custom cards.
  const modalConfirmMessage = ref('')
  let modalConfirmAction: (() => void | Promise<void>) | null = null
  function openConfirmModal(message: string, action: () => void | Promise<void>) {
    modalConfirmMessage.value = message
    modalConfirmAction = action
    modal.value = { type: 'confirm', name: '', renameTarget: '', origin: '', template: '', avatar: '', avatarTarget: '', busy: false }
  }
  // Information-only popup (no follow-up action), e.g. the administrator
  // privilege warning shown when a PC-client instance cannot start.
  const modalAlertTitle = ref('')
  const modalAlertMessage = ref('')
  function openAlertModal(title: string, message: string) {
    modalAlertTitle.value = title
    modalAlertMessage.value = message
    modal.value = { type: 'alert', name: '', renameTarget: '', origin: '', template: '', avatar: '', avatarTarget: '', busy: false }
  }
  async function confirmModal() {
    const m = modal.value
    if (m.busy) return
    if (m.type === 'alert') { m.type = ''; return }
    m.busy = true
    try {
      if (m.type === 'create') {
        const name = m.name.trim()
        if (!name) return
        await api.post('/api/instances', { name, origin: m.origin, avatar: m.avatar })
      } else if (m.type === 'avatar') {
        await api.post(`/api/${m.avatarTarget}/avatar`, { avatar: m.avatar })
      } else if (m.type === 'rename') {
        const newName = m.name.trim()
        if (!newName || newName === m.renameTarget) return
        await api.post(`/api/instances/${m.renameTarget}/rename`, { name: newName })
        // Keep the workspace route pointing at the renamed instance.
        const selectedName = String(router.currentRoute.value.params.name || '')
        if (selectedName === m.renameTarget) {
          const old = m.renameTarget
          m.renameTarget = newName
          router.replace(router.currentRoute.value.path.replace(`/i/${old}/`, `/i/${newName}/`))
        }
      } else if (m.type === 'delete') {
        await api.del(`/api/${m.name}`)
        if (String(router.currentRoute.value.params.name || '') === m.name) useUiStore().dashboard()
      } else if (m.type === 'confirm') {
        const action = modalConfirmAction
        modalConfirmAction = null
        m.type = ''
        await action?.()
        return
      } else if (m.type === 'resetDeploy') {
        // Theme/language revert to template defaults too; sync the cached theme
        // before reloading so the page restarts on the reverted palette.
        const result = await api.post('/api/system/deploy/reset', { template: m.template })
        localStorage.setItem('nkas-theme', result.theme)
        m.type = ''
        toast.notify(t('已还原为默认值'))
        setTimeout(() => window.location.reload(), 600)
        return
      }
      m.type = ''
      await useInstancesStore().loadInstances()
    } catch (exception: any) { toast.error = exception.message } finally { m.busy = false }
  }
  return {
    modal, originOptions, deployTemplateOptions,
    openCreateModal, openRenameModal, openDeleteModal, openResetDeployModal, openAvatarModal,
    modalConfirmMessage, openConfirmModal, modalAlertTitle, modalAlertMessage, openAlertModal, confirmModal,
  }
})
