<script setup lang="ts">
import { computed, defineAsyncComponent } from 'vue'
import { storeToRefs } from 'pinia'
import AppSelect from '../components/AppSelect.vue'
import LiveLog from '../components/LiveLog.vue'
import FieldItemTable from '../components/config/FieldItemTable.vue'
import FieldPathPicker from '../components/config/FieldPathPicker.vue'
import FieldPriority from '../components/config/FieldPriority.vue'
import { highlightTextarea, isStructuredTextarea, onTextareaInput, vAutosize } from '../composables/useTextarea'
import { useRouteInfo } from '../composables/useRouteInfo'
import { t } from '../i18n'
import { onTextInput } from '../utils'
import { useBlaLoginStore } from '../stores/blaLogin'
import { useInstancesStore } from '../stores/instances'
import { useToastStore } from '../stores/toast'
import { useWorkspaceStore } from '../stores/workspace'

// ECharts is only needed by one read-only statistics field.  Loading it on
// demand keeps normal configuration and scheduler pages within the first-load
// budget defined by the UI plan.
const FieldInterception = defineAsyncComponent(() => import('../components/config/FieldInterception.vue'))

const { selectedPage, selectedTask } = useRouteInfo()
const workspace = useWorkspaceStore()
const { schemaReady, collapsed, activeGroup, taskSchema, importBusy, notifyTestBusy } = storeToRefs(workspace)
const { isWideField, save, saveValue, datetimeValue, scheduleDatetimeSave, flushDatetimeSave, clearField, pickedPath, importInterception, testNotify, startTool } = workspace
const instancesStore = useInstancesStore()
const { lifecycle } = instancesStore
const selectedInstance = computed(() => instancesStore.instances.find(item => item.name === workspace.selectedName))
const { blaLoginBusy } = storeToRefs(useBlaLoginStore())
const { startBlaLogin } = useBlaLoginStore()
const toast = useToastStore()

function groupId(group: any) { return `group-${group.key}` }
function onViewScroll(event: Event) {
  const groups = taskSchema.value?.groups || []
  if (!groups.length) return
  let current = groups[0].key
  for (const group of groups) {
    const el = document.getElementById(groupId(group))
    if (el && el.getBoundingClientRect().top <= 140) current = group.key
  }
  activeGroup.value = current
}
function jumpToGroup(group: any) { activeGroup.value = group.key; document.getElementById(groupId(group))?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
</script>

<template>
  <section class="view" :class="{ 'tool-view': selectedPage === 'tool' }" @scroll.passive="onViewScroll">
    <div class="task-layout">
      <div>
        <article class="card task-hero">
          <div class="task-icon">{{ selectedPage === 'tool' ? '🛠' : '⚙️' }}</div>
          <div style="flex:1"><h2>{{ taskSchema?.name || selectedTask }}</h2><div class="sub">{{ taskSchema?.help || '' }}</div></div>
          <button v-if="selectedPage === 'tool'" class="btn" :class="selectedInstance?.state === 1 ? 'danger' : 'primary'" @click="selectedInstance?.state === 1 ? lifecycle('stop') : startTool()">{{ selectedInstance?.state === 1 ? t('停止') : `▶ ${t('启动')}` }}</button>
        </article>
        <div class="cfg-groups">
          <article v-for="group in taskSchema?.groups || []" :id="groupId(group)" :key="group.key" class="card group-card" :class="{ collapsed: collapsed[group.key] }">
            <button class="group-head" @click="collapsed[group.key] = !collapsed[group.key]">
              <h4>{{ group.name }}</h4>
              <span class="group-summary">›</span>
            </button>
            <div class="group-body">
              <div v-if="selectedTask === 'BlaAuth' && group.key === 'BlaAuth'" class="field">
                <div class="field-label"><div class="fname">{{ t('登录获取 Cookie（BETA）') }}</div><div class="fhelp">{{ t('使用NKAS设置-账号设置中的LiPass账号自动登录妮游社，成功后自动填写 Cookie 和 XCommonParams；如出现滑块验证码，在弹窗中的图片上拖动完成。') }}</div></div>
                <div class="field-control"><button class="btn primary" :disabled="blaLoginBusy" @click="startBlaLogin">{{ blaLoginBusy ? t('登录中…') : t('一键登录') }}</button></div>
              </div>
              <div v-for="field in group.fields" :key="field.key" :id="`field-${field.key}`" class="field" :class="{ 'field-wide': isWideField(field) }">
                <div class="field-label"><div class="fname">{{ field.title }}</div><div v-if="field.help" class="fhelp">{{ field.help }}</div></div>
                <div class="field-control">
                  <label v-if="field.widget === 'checkbox'" class="switch"><input type="checkbox" :checked="field.value" :disabled="field.display !== 'show'" @change="save(field, $event)"><span class="slider"></span></label>
                  <AppSelect v-else-if="field.widget === 'select'" :model-value="field.value" :options="field.options" :disabled="field.display !== 'show'" @change="(value: any) => saveValue(field, value).catch(() => {})"/>
                  <template v-else-if="field.path_picker">
                    <div class="path-field">
                      <input type="text" :value="field.value" :readonly="field.display !== 'show'" @input="onTextInput(field, $event)" @change="save(field, $event)">
                      <FieldPathPicker :value="field.value" :picker="field.path_picker" :disabled="field.display !== 'show'" @picked="pickedPath(field, $event)" @error="toast.error = $event"/>
                    </div>
                  </template>
                  <div v-else-if="field.widget === 'textarea'" class="code-wrap" :class="{ 'code-wrap-resizable': isStructuredTextarea(field) }">
                    <pre v-if="isStructuredTextarea(field)" class="code-highlight" v-html="highlightTextarea(field)"></pre>
                    <textarea v-autosize :class="{ 'code-input': isStructuredTextarea(field), 'textarea-mono': field.mode !== 'text' }" :value="field.value" :readonly="field.display !== 'show'" :inputmode="field.mode === 'url' ? 'url' : 'text'" spellcheck="false" @input="onTextareaInput(field, $event)" @change="save(field, $event)"></textarea>
                  </div>
                  <FieldItemTable v-else-if="field.widget === 'item_table'" :data="field.special_data" :loading="!field.special_data"/>
                  <FieldPriority v-else-if="field.widget === 'priority'" :value="field.value" :options="field.options" :disabled="field.display !== 'show'" :placeholder="t('添加')" @change="(value: string) => saveValue(field, value).catch(() => {})"/>
                  <FieldInterception v-else-if="field.widget === 'interception_stone_import'" :widget="field.widget" :busy="Boolean(importBusy[field.key])" @import="importInterception(field, $event)" @error="toast.error = $event"/>
                  <FieldInterception v-else-if="field.widget === 'interception_stone_charts'" :widget="field.widget" :data="field.special_data"/>
                  <template v-else-if="field.widget === 'datetime'">
                    <div class="dt-field">
                      <input type="datetime-local" :value="datetimeValue(field.value)" :readonly="field.display !== 'show'" @input="scheduleDatetimeSave(field, $event)" @blur="flushDatetimeSave(field, $event)">
                      <button v-if="field.display === 'show'" type="button" class="dt-clear" :title="t('清空')" @mousedown.prevent @click="clearField(field)">✕</button>
                    </div>
                  </template>
                  <input v-else :type="field.key.endsWith('.Password') ? 'password' : 'text'" :value="field.value" :readonly="field.display !== 'show'" @input="onTextInput(field, $event)" @change="save(field, $event)">
                </div>
              </div>
              <div v-if="selectedTask === 'NKAS' && group.key === 'Notification'" class="field">
                <div class="field-label"><div class="fname">{{ t('测试通知') }}</div><div class="fhelp">{{ t('发送一条测试通知，验证当前通知设置是否生效。') }}</div></div>
                <div class="field-control"><button class="btn" :disabled="notifyTestBusy" @click="testNotify">{{ notifyTestBusy ? t('发送中…') : t('测试通知') }}</button></div>
              </div>
            </div>
          </article>
        </div>
        <article v-if="selectedTask && schemaReady && !taskSchema" class="card group-card">
          <div class="group-body special-empty" style="padding:16px 22px">{{ t('未知任务') }}: {{ selectedTask }}</div>
        </article>
        <LiveLog v-if="selectedPage === 'tool'" class="tool-log" />
      </div>
      <aside class="card anchor-nav">
        <div class="side-label">{{ t('本页分组') }}</div>
        <button v-for="group in taskSchema?.groups || []" :key="group.key" class="anchor-nav-item" :class="{ active: activeGroup === group.key }" @click="jumpToGroup(group)">{{ group.name }}</button>
      </aside>
    </div>
  </section>
</template>
