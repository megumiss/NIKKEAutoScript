import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import type { Announcement } from '../types'
import { useToastStore } from './toast'

// Announcement center: persistent announcements (with history) from the
// backend, shown in a master-detail modal. Read state changes only after
// an explicit acknowledgement has been persisted successfully.
export const useAnnouncementsStore = defineStore('announcements', () => {
  const toast = useToastStore()
  const announcements = ref<Announcement[]>([])
  const announcementCenterOpen = ref(false)
  const activeAnnouncementId = ref('')
  const markingReadId = ref('')
  // 每会话仅自动弹出一次（由 system store 的 loadSystem 置位）
  const autoShown = ref(false)
  const unreadAnnouncementCount = computed(() => announcements.value.filter(item => !item.read).length)
  const activeAnnouncement = computed(() => announcements.value.find(item => item.id === activeAnnouncementId.value))
  function openAnnouncementCenter() {
    announcementCenterOpen.value = true
    const target = announcements.value.find(item => !item.read) || announcements.value[0]
    if (target) selectAnnouncement(target)
  }
  function selectAnnouncement(announcement: Announcement) {
    // 查看不等于确认已读，切换公告不改变阅读状态
    activeAnnouncementId.value = announcement.id
  }
  async function markAnnouncementRead() {
    const announcement = activeAnnouncement.value
    if (!announcement || announcement.read || markingReadId.value) return
    const id = announcement.id
    markingReadId.value = id
    try {
      await api.post('/api/system/notices/read', { ids: [id] })
      // 保存期间公告可能切换或被重新加载，只更新请求对应的当前记录
      const current = announcements.value.find(item => item.id === id)
      if (current) current.read = true
    } catch (exception: unknown) {
      toast.error = exception instanceof Error ? exception.message : t('标记已读失败，请重试')
    } finally {
      markingReadId.value = ''
    }
  }
  return { announcements, announcementCenterOpen, activeAnnouncementId, markingReadId, autoShown, unreadAnnouncementCount, activeAnnouncement, openAnnouncementCenter, selectAnnouncement, markAnnouncementRead }
})
