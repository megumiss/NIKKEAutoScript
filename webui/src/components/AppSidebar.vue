<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import AppSelect from './AppSelect.vue'
import { useTauriShell } from '../composables/useTauriShell'
import { useRouteInfo } from '../composables/useRouteInfo'
import { languageOptions, t } from '../i18n'
import { brandIcon } from '../utils'
import { useInstancesStore } from '../stores/instances'
import { useSystemStore } from '../stores/system'
import { useUiStore } from '../stores/ui'

const router = useRouter()
const { isDashboard, isManage, isSettings, isDeploy, isLogs, isLinks, isAbout, selectedName } = useRouteInfo()
const instancesStore = useInstancesStore()
const { instances } = storeToRefs(instancesStore)
const { displayStatus, displayStatusClass, initials } = instancesStore
const { systemStatus } = storeToRefs(useSystemStore())
const { toggleTheme, setLanguage } = useSystemStore()
const ui = useUiStore()
const { mobileNav, sidebarCollapsed } = storeToRefs(ui)
const { dashboard, enter } = ui
const { onWindowDragAreaMouseDown } = useTauriShell()
function onBrandMouseDown(event: MouseEvent) { onWindowDragAreaMouseDown(event, '.side-toggle') }
</script>

<template>
  <nav class="sidebar" :class="{ 'mobile-open': mobileNav === 'sidebar' }">
    <div class="brand" @mousedown="onBrandMouseDown">
      <img class="brand-logo brand-logo-img" :src="brandIcon" alt="NKAS">
      <div class="brand-text"><div class="brand-name">NKAS</div><div class="brand-sub">NIKKE AUTO SCRIPT</div></div>
      <button class="side-toggle" @click="sidebarCollapsed = !sidebarCollapsed">{{ sidebarCollapsed ? '»' : '«' }}</button>
    </div>
    <div class="side-section">
      <button class="side-item" :class="{ active: isDashboard }" @click="dashboard"><span class="sicon">📊</span><span class="side-text">{{ t('总览') }}</span></button>
    </div>
    <div class="side-section">
      <div class="side-label">{{ t('实例') }}</div>
      <button v-for="instance in instances" :key="instance.name" class="side-item" :class="{ active: selectedName === instance.name }" @click="enter(instance.name)">
        <span class="inst-avatar" :class="{ idle: displayStatusClass(instance.name, instance.state, instance.current_task) === 'idle' }">{{ initials(instance.name) }}<span class="ring" :class="displayStatusClass(instance.name, instance.state, instance.current_task)"></span></span>
        <span class="side-text" :title="instance.name">{{ instance.name }}</span>
        <span class="badge" :class="{ 'idle-badge': displayStatusClass(instance.name, instance.state, instance.current_task) === 'idle', 'error-badge': displayStatusClass(instance.name, instance.state, instance.current_task) === 'error' }">{{ displayStatus(instance.name, instance.state, instance.current_task) }}</span>
      </button>
    </div>
    <div class="side-section">
      <div class="side-label">{{ t('系统') }}</div>
      <button class="side-item" :class="{ active: isManage }" @click="router.push('/manage')"><span class="sicon">🗂</span><span class="side-text">{{ t('多开') }}</span></button>
      <button class="side-item" :class="{ active: isDeploy }" @click="router.push('/deploy')"><span class="sicon">📦</span><span class="side-text">{{ t('部署') }}</span></button>
      <button class="side-item" :class="{ active: isLogs }" @click="router.push('/logs')"><span class="sicon">📄</span><span class="side-text">{{ t('日志') }}</span></button>
      <button class="side-item" :class="{ active: isSettings }" @click="router.push('/settings')"><span class="sicon">⚙️</span><span class="side-text">{{ t('更新') }}</span></button>
      <button class="side-item" :class="{ active: isAbout }" @click="router.push('/about')"><span class="sicon">ℹ️</span><span class="side-text">{{ t('关于') }}</span></button>
    </div>
    <div class="side-section">
      <div class="side-label">{{ t('其他') }}</div>
      <button class="side-item" :class="{ active: isLinks }" @click="router.push('/links')"><span class="sicon">🌐</span><span class="side-text">{{ t('常用链接') }}</span></button>
    </div>
    <div class="side-spacer"></div>
    <div class="side-footer">
      <button class="icon-btn" @click="toggleTheme">{{ systemStatus.theme === 'dark' ? '🌙' : '☀️' }} <span class="side-text">{{ t('主题') }}</span></button>
      <AppSelect class="lang-select" :model-value="systemStatus.language" :options="languageOptions" @change="setLanguage"/>
    </div>
  </nav>
</template>
