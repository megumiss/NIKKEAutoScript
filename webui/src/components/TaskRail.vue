<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useRouter } from 'vue-router'
import { useRouteInfo } from '../composables/useRouteInfo'
import { t } from '../i18n'
import { useInstancesStore } from '../stores/instances'
import { useUiStore } from '../stores/ui'
import { useWorkspaceStore } from '../stores/workspace'

const router = useRouter()
const { selectedPage, selectedTask } = useRouteInfo()
const workspace = useWorkspaceStore()
const { schemaReady, railCollapsed, taskFilter, visibleMenus, selectedName } = storeToRefs(workspace)
const { taskEnabled } = workspace
const instancesStore = useInstancesStore()
const { displayStatus, displayStatusClass, initials } = instancesStore
const selectedInstance = computed(() => instancesStore.instances.find(item => item.name === selectedName.value))
const { mobileNav } = storeToRefs(useUiStore())

function toggleRail(menu: any) { railCollapsed.value[menu.key] = !railCollapsed.value[menu.key] }
function openTask(task: any, page: string) { mobileNav.value = ''; router.push(`/i/${selectedName.value}/${page}/${task.key}`) }
function openField(task: any, field: any) {
  mobileNav.value = ''
  taskFilter.value = ''
  if (workspace.collapsed[field.groupKey]) workspace.collapsed[field.groupKey] = false
  router.push(`/i/${selectedName.value}/${field.page}/${task.key}`)
  const id = `field-${field.key}`
  let tries = 0
  const scrollToField = () => {
    const el = document.getElementById(id)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      el.classList.add('field-flash')
      setTimeout(() => el.classList.remove('field-flash'), 1600)
    } else if (++tries < 20) {
      setTimeout(scrollToField, 100)
    }
  }
  setTimeout(scrollToField, 200)
}
</script>

<template>
  <aside class="rail" :class="{ 'mobile-open': mobileNav === 'rail' }">
    <div class="rail-head">
      <div class="rail-inst">
        <span class="inst-avatar" :class="{ idle: displayStatusClass(selectedName, selectedInstance?.state, selectedInstance?.current_task) === 'idle' }">{{ initials(selectedName) }}<span class="ring" :class="displayStatusClass(selectedName, selectedInstance?.state, selectedInstance?.current_task)"></span></span>
        <div class="rail-inst-info"><div class="rail-inst-name" :title="selectedName">{{ selectedName }}</div><div class="rail-inst-state">{{ displayStatus(selectedName, selectedInstance?.state, selectedInstance?.current_task) }}</div></div>
      </div>
      <label class="rail-search">🔍 <input v-model="taskFilter" :placeholder="t('筛选任务/设置')"><button v-if="taskFilter" type="button" class="rail-clear" @click.prevent="taskFilter = ''">✕</button></label>
    </div>
    <div class="rail-list">
      <div class="rail-top">
        <button class="rail-item" :class="{ active: selectedPage === 'overview' }" @click="router.push(`/i/${selectedName}/overview`)"><span class="sicon">📈</span>{{ t('任务总览') }}</button>
        <button class="rail-item" :class="{ active: selectedPage === 'schedule' }" @click="router.push(`/i/${selectedName}/schedule`)"><span class="sicon">🗓️</span>{{ t('调度设置') }}(BETA)</button>
      </div>
      <template v-if="schemaReady" v-for="menu in visibleMenus" :key="menu.key">
        <button class="rail-group" :class="{ expanded: !railCollapsed[menu.key] || taskFilter }" @click="toggleRail(menu)">
          <span class="chev">›</span><span class="sicon">{{ menu.icon || '•' }}</span>{{ menu.name }}
          <span class="rail-count">{{ menu.tasks.filter((task: any) => taskEnabled(task.key)).length }}/{{ menu.tasks.length }}</span>
        </button>
        <div v-show="!railCollapsed[menu.key] || taskFilter" class="rail-tasks">
          <template v-for="task in menu.tasks" :key="task.key">
            <button class="rail-item" :class="{ active: selectedTask === task.key }" @click="openTask(task, menu.page === 'tool' ? 'tool' : 'task')">
              {{ task.name }}{{ task.key === 'PhysicalDevice' ? '(BETA)' : '' }}
              <span v-if="selectedInstance?.current_task === task.key" class="spin"></span>
              <span v-else-if="taskEnabled(task.key)" class="mini-dot on"></span>
            </button>
            <div v-if="task.matchedFields?.length" class="rail-field-list">
              <button v-for="field in task.matchedFields" :key="field.key" class="rail-item rail-field" @click="openField(task, field)">
                <span class="field-ico">⚙️</span><span class="field-name">{{ field.title }}</span>
              </button>
            </div>
          </template>
        </div>
      </template>
    </div>
  </aside>
</template>
