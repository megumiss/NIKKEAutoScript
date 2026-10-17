<script setup lang="ts">
import { ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import AppIcon from './AppIcon.vue'
import { t } from '../i18n'
import { useAnnouncementsStore } from '../stores/announcements'

const announcementsStore = useAnnouncementsStore()
const { announcements, announcementCenterOpen, activeAnnouncementId, activeAnnouncement, markingReadId } = storeToRefs(announcementsStore)
const { selectAnnouncement, markAnnouncementRead } = announcementsStore

// 更新历史的展开状态只存在本地，换公告或重新打开时默认展开首条
const openHistoryId = ref('')
watch([activeAnnouncement, announcementCenterOpen], () => {
  openHistoryId.value = activeAnnouncement.value?.items?.[0]?.id || ''
}, { immediate: true })
function toggleHistoryItem(id: string) { openHistoryId.value = openHistoryId.value === id ? '' : id }
</script>

<template>
  <div v-if="announcementCenterOpen" class="modal-mask" @click.self="announcementCenterOpen = false">
    <div class="modal-card announcement-card">
      <div class="announcement-head">
        <h3>{{ t('公告中心') }}</h3>
        <button class="tb-btn announcement-close" :title="t('关闭')" @click="announcementCenterOpen = false"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6 6l8 8M14 6l-8 8"/></svg></button>
      </div>
      <div class="announcement-body">
        <div class="announcement-list">
          <div v-if="!announcements.length" class="announcement-empty">{{ t('暂无公告') }}</div>
          <button v-for="item in announcements" :key="item.id" class="announcement-item" :class="[{ active: item.id === activeAnnouncementId }, item.type]" @click="selectAnnouncement(item)">
            <span class="announcement-date">
              <svg v-if="item.pinned" class="announcement-pin" :title="t('置顶')" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12.9 3.3 16.7 7.1l-2 .55a3.2 3.2 0 0 0-1.62 1l-1.2 1.5-3.03-3.03 1.5-1.2a3.2 3.2 0 0 0 1-1.62l.55-2Z"/><path d="m8.85 11.15-4.6 4.6"/></svg>
              {{ item.date }}
            </span>
            <span class="announcement-title">{{ item.title }}</span>
            <span v-if="!item.read" class="announcement-dot" :title="t('未读')"></span>
          </button>
        </div>
        <div class="announcement-view">
          <div class="announcement-scroll">
            <!-- 公告正文来自仓库自带的公告文件（可信来源），用 v-html 渲染以支持
                 链接与强调；notice-content 的 pre-line 让纯文本公告照常换行 -->
            <!-- 带 items 的公告（更新历史）按条目折叠展示，条目正文同样来自公告文件 -->
            <div v-if="activeAnnouncement?.items?.length" class="announcement-content announcement-history">
              <div v-for="item in activeAnnouncement.items" :key="item.id" class="announcement-fold" :class="{ open: openHistoryId === item.id }">
                <button class="announcement-fold-head" :aria-expanded="openHistoryId === item.id" @click="toggleHistoryItem(item.id)">
                  <svg class="announcement-fold-arrow" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m6 8 4 4 4-4"/></svg>
                  <span class="announcement-fold-date">{{ item.date }}</span>
                  <span class="announcement-fold-title">{{ item.title }}</span>
                </button>
                <div v-show="openHistoryId === item.id" class="announcement-fold-body modal-text notice-content announcement-content" v-html="item.content"></div>
              </div>
            </div>
            <div v-else class="modal-text notice-content announcement-content" v-html="activeAnnouncement?.content || ''"></div>
          </div>
        </div>
      </div>
      <div v-if="activeAnnouncement" class="announcement-footer">
        <div class="announcement-footer-copy">
          <span class="announcement-footer-title" :title="activeAnnouncement.title">{{ activeAnnouncement.title }}</span>
          <span class="announcement-footer-hint">{{ t(activeAnnouncement.items?.length ? '仅标记本组更新历史，不影响其他公告' : '仅标记当前公告，不会关闭窗口') }}</span>
        </div>
        <div class="announcement-footer-action" aria-live="polite">
          <span v-if="activeAnnouncement.read" class="announcement-read-state"><AppIcon name="check" :size="16" /> {{ t('已读') }}</span>
          <button v-else type="button" class="btn accent announcement-read-action" :disabled="!!markingReadId" :aria-busy="markingReadId === activeAnnouncement.id" @click="markAnnouncementRead">
            <span v-if="markingReadId === activeAnnouncement.id" class="btn-spin" aria-hidden="true"></span>
            <AppIcon v-else name="check" :size="15" />
            {{ t(markingReadId === activeAnnouncement.id ? '保存中…' : activeAnnouncement.items?.length ? '全部历史标为已读' : '标为已读') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
