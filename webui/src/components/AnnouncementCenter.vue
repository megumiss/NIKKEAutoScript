<script setup lang="ts">
import { storeToRefs } from 'pinia'
import AppIcon from './AppIcon.vue'
import { t } from '../i18n'
import { useAnnouncementsStore } from '../stores/announcements'

const announcementsStore = useAnnouncementsStore()
const { announcements, announcementCenterOpen, activeAnnouncementId, activeAnnouncement } = storeToRefs(announcementsStore)
const { selectAnnouncement, markAnnouncementRead } = announcementsStore
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
            <div class="modal-text notice-content announcement-content" v-html="activeAnnouncement?.content || ''"></div>
          </div>
          <button v-if="activeAnnouncement && !activeAnnouncement.read" class="btn primary" @click="markAnnouncementRead"><AppIcon name="check" :size="15" /> {{ t('我知道了') }}</button>
        </div>
      </div>
    </div>
  </div>
</template>
