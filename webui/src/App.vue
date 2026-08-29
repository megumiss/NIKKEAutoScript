<script setup lang="ts">
import { onBeforeUnmount, onMounted, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import AnnouncementCenter from './components/AnnouncementCenter.vue'
import AppModal from './components/AppModal.vue'
import AppSidebar from './components/AppSidebar.vue'
import AppTopbar from './components/AppTopbar.vue'
import BlaLoginModal from './components/BlaLoginModal.vue'
import MaintenanceBanner from './components/MaintenanceBanner.vue'
import TaskRail from './components/TaskRail.vue'
import { useRouteInfo } from './composables/useRouteInfo'
import { useTauriShell } from './composables/useTauriShell'
import { t } from './i18n'
import AboutView from './views/AboutView.vue'
import DashboardView from './views/DashboardView.vue'
import DeployView from './views/DeployView.vue'
import LinksView from './views/LinksView.vue'
import LogsView from './views/LogsView.vue'
import ManageView from './views/ManageView.vue'
import OverviewView from './views/OverviewView.vue'
import ScheduleView from './views/ScheduleView.vue'
import SettingsView from './views/SettingsView.vue'
import TaskView from './views/TaskView.vue'
import ToolsView from './views/ToolsView.vue'
import { useDeployStore } from './stores/deploy'
import { useInstancesStore } from './stores/instances'
import { useLinksStore } from './stores/links'
import { useLogsPageStore } from './stores/logsPage'
import { useSystemStore } from './stores/system'
import { useToastStore } from './stores/toast'
import { useUiStore } from './stores/ui'
import { useUpdateStore } from './stores/update'
import { useWorkspaceStore } from './stores/workspace'

const route = useRoute()
const router = useRouter()
const { selectedName, selectedPage, isDashboard, isManage, isSettings, isDeploy, isLogs, isTools, isLinks, isWorkspace } = useRouteInfo()

const toast = useToastStore()
const { toasts } = storeToRefs(toast)
const { closeToast } = toast
const system = useSystemStore()
const { systemStatus, notices, backendDown } = storeToRefs(system)
const { loadSystem, dismissNotice, healthCheck, autoUpdateTitle, autoUpdatePreview } = system
const instancesStore = useInstancesStore()
const { loadInstances } = instancesStore
const workspace = useWorkspaceStore()
const { loadWorkspace, startStateSocket, startSockets } = workspace
const update = useUpdateStore()
const deploy = useDeployStore()
const logsPage = useLogsPageStore()
const links = useLinksStore()
const { mobileNav, sidebarCollapsed } = storeToRefs(useUiStore())
const { isTauri, syncMaximized } = useTauriShell()

function isLegacyElectronLayout() { return window.parent !== window }
const legacyElectron = isLegacyElectronLayout()

let healthTimer: number | undefined
onMounted(async () => {
  if (sessionStorage.getItem('nkas-desktop-updated')) { sessionStorage.removeItem('nkas-desktop-updated'); toast.notify(t('启动器更新完成'), 'ok', 4000) }
  if (isTauri) { syncMaximized(); window.addEventListener('resize', syncMaximized) }
  await loadSystem()
  await update.notifyDesktopUpdate()
  await loadInstances()
  if (route.path === '/' && systemStatus.value.home_page === 'instance' && instancesStore.instances.length) { router.replace(`/i/${instancesStore.instances[0].name}/overview`) } else { await loadWorkspace() }
  startStateSocket()
  startSockets()
  if (isDeploy.value) await deploy.loadDeploy()
  if (isLogs.value) await logsPage.refreshLogs()
  if (isLinks.value) await links.loadWebLinks()
  healthTimer = window.setInterval(healthCheck, 4000)
})
watch(() => route.fullPath, async () => {
  // 路由变化时收起移动端抽屉，避免残留遮挡主内容。
  mobileNav.value = ''
  // Only a different instance needs a schema reload and socket swap; task
  // switches and global pages retain the current instance's log scrollback.
  if (selectedName.value && selectedName.value !== workspace.workspaceName) {
    workspace.logs = []
    await loadWorkspace()
  }
  workspace.activeGroup = workspace.taskSchema?.groups?.[0]?.key || ''
  startSockets()
  if (isSettings.value) { await update.refreshUpdateInfo(); await update.refreshDesktopUpdate() }
  else { update.clearPollTimers() }
  if (isDeploy.value) await deploy.loadDeploy()
  if (isLogs.value) await logsPage.refreshLogs()
  if (isLinks.value && !links.webLoaded) await links.loadWebLinks()
})
// 抽屉打开时锁定背景滚动，避免移动端误触翻页。
watch(mobileNav, open => { document.body.style.overflow = open ? 'hidden' : '' })
onBeforeUnmount(() => {
  document.body.style.overflow = ''
  window.removeEventListener('resize', syncMaximized)
  workspace.closeSockets()
  window.clearInterval(healthTimer)
  update.clearPollTimers()
  workspace.clearDatetimeSaveTimers()
})
</script>

<template>
  <div class="app" :class="{ 'legacy-electron': legacyElectron, 'side-collapsed': sidebarCollapsed }">
    <div class="app-body">
    <AppSidebar />
    <TaskRail v-if="isWorkspace" />
    <main class="main">
      <AppTopbar />
      <MaintenanceBanner :language="systemStatus.language" />
      <div v-if="notices.length" class="notice-stack">
        <article v-for="notice in notices" :key="notice.key" class="notice-card" :class="notice.type">
          <div>
            <strong>{{ notice.key === 'auto_update' ? autoUpdateTitle(notice.data) : notice.key === 'auto_update_failed' ? t('自动更新失败') : (notice.data.title || t('系统通知')) }}</strong>
            <ul v-if="autoUpdatePreview(notice.data).length" class="notice-messages">
              <li v-for="(msg, index) in autoUpdatePreview(notice.data)" :key="index">• {{ msg }}</li>
            </ul>
            <!-- Startup auto-update failure: explain what happened in the
                 user's language, keep the raw git error as detail. -->
            <template v-else-if="notice.key === 'auto_update_failed'">
              <p>{{ t('启动时的自动更新未成功，已跳过更新并继续使用当前版本。') }}</p>
              <p v-if="notice.data.error" class="notice-error-detail">{{ notice.data.error }}</p>
            </template>
            <p v-else>{{ notice.data.content || notice.data.error || t('有新的系统通知。') }}</p>
          </div>
          <button class="btn sm" @click="dismissNotice(notice)">{{ t('知道了') }}</button>
        </article>
      </div>
      <div class="toast-stack">
        <div v-for="toastItem in toasts" :key="toastItem.id" class="toast" :class="toastItem.kind"><span>{{ toastItem.kind === 'error' ? '✕' : '✓' }} {{ toastItem.text }}</span><button v-if="toastItem.action" type="button" class="toast-action" @click="toastItem.action.run(); closeToast(toastItem.id)">{{ toastItem.action.label }}</button><button type="button" class="toast-close" @click="closeToast(toastItem.id)">✕</button></div>
      </div>
      <DashboardView v-if="isDashboard" />
      <OverviewView v-else-if="isWorkspace && selectedPage === 'overview'" />
      <ScheduleView v-else-if="isWorkspace && selectedPage === 'schedule'" />
      <TaskView v-else-if="isWorkspace" />
      <ManageView v-else-if="isManage" />
      <SettingsView v-else-if="isSettings" />
      <DeployView v-else-if="isDeploy" />
      <LogsView v-else-if="isLogs" />
      <LinksView v-else-if="isLinks" />
      <ToolsView v-else-if="isTools" />
      <AboutView v-else />
    </main>
    </div>
    <!-- 移动端：贴右边缘的悬浮把手，点击滑出/收起任务列表抽屉 -->
    <button v-if="isWorkspace" class="rail-trigger" :class="{ open: mobileNav === 'rail' }" :title="mobileNav === 'rail' ? t('收起任务列表') : t('任务列表')" @click="mobileNav = mobileNav === 'rail' ? '' : 'rail'">
      <svg v-if="mobileNav === 'rail'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m10 6 6 6-6 6"/></svg>
      <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m14 6-6 6 6 6"/></svg>
    </button>
    <div v-if="mobileNav" class="mobile-scrim" @click="mobileNav = ''"></div>
    <div v-if="backendDown" class="backend-down"><div class="backend-down-card">{{ t('后端连接中断，正在等待恢复…') }}</div></div>
    <AnnouncementCenter />
    <AppModal />
    <BlaLoginModal />
  </div>
</template>
