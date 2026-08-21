import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import type { Announcement } from '../types'
import { useToastStore } from './toast'

// Announcement center: persistent announcements (with history) from the
// backend, shown in a master-detail modal.  `read` comes from the server and
// is flipped locally as soon as an entry is opened.
export const useAnnouncementsStore = defineStore('announcements', () => {
  const toast = useToastStore()
  const announcements = ref<Announcement[]>([])
  const announcementCenterOpen = ref(false)
  const activeAnnouncementId = ref('')
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
    // 仅切换查看的公告；已读必须由用户显式点击「我知道了」触发
    activeAnnouncementId.value = announcement.id
  }
  async function markAnnouncementRead() {
    const announcement = activeAnnouncement.value
    if (!announcement || announcement.read) return
    // Optimistic local read so the badge updates immediately; roll back when
    // the mark-read call fails.
    announcement.read = true
    try { await api.post('/api/system/notices/read', { ids: [announcement.id] }) } catch (exception: any) { announcement.read = false; toast.error = exception.message }
  }
  return { announcements, announcementCenterOpen, activeAnnouncementId, autoShown, unreadAnnouncementCount, activeAnnouncement, openAnnouncementCenter, selectAnnouncement, markAnnouncementRead }
})
