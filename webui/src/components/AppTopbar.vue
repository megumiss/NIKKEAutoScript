<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useTauriShell } from '../composables/useTauriShell'
import { useRouteInfo } from '../composables/useRouteInfo'
import { t } from '../i18n'
import { useAnnouncementsStore } from '../stores/announcements'
import { useInstancesStore } from '../stores/instances'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const { selectedName, selectedPage, isDashboard, isManage, isSettings, isDeploy, isLogs, isLinks, isAbout, isWorkspace } = useRouteInfo()
const workspace = useWorkspaceStore()
const { taskSchema, selectedTask } = storeToRefs(workspace)
const instancesStore = useInstancesStore()
const { displayStatus, displayStatusClass } = instancesStore
const selectedInstance = computed(() => instancesStore.instances.find(item => item.name === selectedName.value))
const announcements = useAnnouncementsStore()
const { unreadAnnouncementCount } = storeToRefs(announcements)
const { openAnnouncementCenter } = announcements
const { mobileNav } = storeToRefs(useUiStore())
const { isTauri, isMaximized, tbMinimize, tbToggleMaximize, tbHide, tbClose, onWindowDragAreaMouseDown } = useTauriShell()
function onTopbarMouseDown(event: MouseEvent) { onWindowDragAreaMouseDown(event, '.tb-btn') }

function pageTitle() { return isDashboard.value ? t('总览') : isManage.value ? t('多开') : isSettings.value ? t('更新') : isDeploy.value ? t('部署') : isLogs.value ? t('日志') : isLinks.value ? t('常用链接') : isAbout.value ? t('关于') : selectedPage.value === 'overview' ? t('任务总览') : selectedPage.value === 'schedule' ? `${t('调度设置')}(BETA)` : taskSchema.value?.name || selectedTask.value }
</script>

<template>
  <header class="topbar" @mousedown="onTopbarMouseDown">
    <button class="tb-btn tb-menu" :title="t('菜单')" @click="mobileNav = mobileNav === 'sidebar' ? '' : 'sidebar'">
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3 5.5h14M3 10h14M3 14.5h14"/></svg>
    </button>
    <div class="crumb"><span v-if="isWorkspace" class="pre">{{ selectedName }} /</span><span class="cur">{{ pageTitle() }}</span></div>
    <span v-if="isWorkspace" class="status-pill" :class="displayStatusClass(selectedName, selectedInstance?.state, selectedInstance?.current_task)">{{ displayStatus(selectedName, selectedInstance?.state, selectedInstance?.current_task) }}</span>
    <div class="topbar-right">
      <button class="tb-btn tb-bell" :title="t('公告中心')" @click="openAnnouncementCenter">
        <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 3.2a4.6 4.6 0 0 0-4.6 4.6v2.6c0 .5-.17 1-.47 1.42l-1.05 1.5h12.24l-1.05-1.5a2.3 2.3 0 0 1-.47-1.42V7.8A4.6 4.6 0 0 0 10 3.2Z"/><path d="M8.3 15.6a1.8 1.8 0 0 0 3.4 0"/></svg>
        <span v-if="unreadAnnouncementCount" class="tb-badge">{{ unreadAnnouncementCount > 99 ? '99+' : unreadAnnouncementCount }}</span>
      </button>
      <template v-if="isTauri">
        <button class="tb-btn" :title="t('隐藏到托盘')" @click="tbHide"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M10 4v7"/><path d="M6.5 8 10 11.5 13.5 8"/><path d="M4 15.5h12"/></svg></button>
        <button class="tb-btn" :title="t('最小化')" @click="tbMinimize"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M5 10.5h10"/></svg></button>
        <button class="tb-btn" :title="isMaximized ? t('还原') : t('最大化')" @click="tbToggleMaximize">
          <svg v-if="!isMaximized" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="5.5" y="5.5" width="9" height="9" rx="1"/></svg>
          <svg v-else viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="4" y="7.5" width="8" height="8" rx="1"/><path d="M8 7.5v-2A1.5 1.5 0 0 1 9.5 4h5A1.5 1.5 0 0 1 16 5.5v5a1.5 1.5 0 0 1-1.5 1.5h-2"/></svg>
        </button>
        <button class="tb-btn tb-close" :title="t('关闭')" @click="tbClose"><svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M6 6l8 8M14 6l-8 8"/></svg></button>
      </template>
    </div>
  </header>
</template>
