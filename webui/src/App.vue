<script setup lang="ts">
import { computed, defineAsyncComponent, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from './api/client'
import { JsonSocket } from './api/ws'
import FieldItemTable from './components/config/FieldItemTable.vue'
import FieldPathPicker from './components/config/FieldPathPicker.vue'
import FieldStorage from './components/config/FieldStorage.vue'

// ECharts is only needed by one read-only statistics field.  Loading it on
// demand keeps normal configuration and scheduler pages within the first-load
// budget defined by the UI plan.
const FieldInterception = defineAsyncComponent(() => import('./components/config/FieldInterception.vue'))

type Instance = { name: string; state: number; mod: string; current_task?: string; next_task?: string }
type Field = { key: string; widget: string; title: string; help: string; value: any; display: string; options: any[]; data_endpoint?: string; path_picker?: any; special_data?: any }
const route = useRoute()
const router = useRouter()
const instances = ref<Instance[]>([])
const schema = ref<any>({ menus: [], tasks: {} })
const queue = ref<any>({ running: [], pending: [], waiting: [] })
const logs = ref<string[]>([])
const logBody = ref<HTMLElement>()
const autoScroll = ref(true)
const error = ref('')
const saved = ref<Record<string, boolean>>({})
const systemStatus = ref<any>({ version: '—', updater_state: 'idle', theme: 'dark', language: 'zh-CN' })
const updateInfo = ref<any>({})
const remoteInfo = ref<any>({})
const notices = ref<any[]>([])
const taskFilter = ref('')
const fieldFilter = ref('')
const collapsed = ref<Record<string, boolean>>({})
const railCollapsed = ref<Record<string, boolean>>({})
const importBusy = ref<Record<string, boolean>>({})
const legacyElectron = window.parent !== window

const selectedName = computed(() => String(route.params.name || ''))
const selectedPage = computed(() => String(route.params.page || ''))
const selectedTask = computed(() => String(route.params.task || ''))
const taskSchema = computed(() => schema.value.tasks[selectedTask.value])
const isDashboard = computed(() => route.path === '/')
const isManage = computed(() => route.path === '/manage')
const isSettings = computed(() => route.path === '/settings')
const isAbout = computed(() => route.path === '/about')
const isWorkspace = computed(() => Boolean(selectedName.value))
const selectedInstance = computed(() => instances.value.find(item => item.name === selectedName.value))
const runningCount = computed(() => instances.value.filter(item => item.state === 1).length)
const visibleMenus = computed(() => schema.value.menus.map((menu: any) => ({ ...menu, tasks: menu.tasks.filter((task: any) => !taskFilter.value || task.name.toLowerCase().includes(taskFilter.value.toLowerCase())) })).filter((menu: any) => menu.tasks.length))
let stateSocket: JsonSocket | undefined
let logSocket: JsonSocket | undefined
let queueSocket: JsonSocket | undefined

function taskEnabled(task: string) { return schema.value.tasks[task]?.groups?.some((group: any) => group.fields.some((field: Field) => field.key.endsWith('.Scheduler.Enable') && field.value)) }
function stateText(state?: number) { return state === 1 ? '调度运行中' : state === 2 ? '空闲' : '已停止或异常' }
function stateClass(state?: number) { return state === 1 ? 'running' : 'idle' }
function initials(name: string) { return name.slice(0, 1).toUpperCase() }
function pageTitle() { return isDashboard.value ? '全局总览' : isManage.value ? '实例管理' : isSettings.value ? '设置 / 更新' : isAbout.value ? '关于' : selectedPage.value === 'overview' ? selectedName.value : taskSchema.value?.name || selectedTask.value }
function allFields() { return Object.values(schema.value.tasks).flatMap((task: any) => task.groups.flatMap((group: any) => group.fields)) as Field[] }
function groupFields(group: any) { const q = fieldFilter.value.trim().toLowerCase(); return q ? group.fields.filter((field: Field) => `${field.title} ${field.help} ${field.key}`.toLowerCase().includes(q)) : group.fields }
function groupId(group: any) { return `group-${group.key}` }
function jumpToGroup(group: any) { document.getElementById(groupId(group))?.scrollIntoView({ behavior: 'smooth', block: 'start' }) }

async function loadInstances() {
  try { instances.value = await api.get('/api/instances'); if (route.path === '/' && instances.value.length === 1) await router.replace(`/i/${instances.value[0].name}/overview`) } catch (exception: any) { error.value = exception.message }
}
async function loadSystem() {
  try {
    systemStatus.value = await api.get('/api/system/status')
    updateInfo.value = await api.get('/api/system/update')
    remoteInfo.value = await api.get('/api/system/remote')
    notices.value = (await api.get('/api/system/notices')).notices || []
    document.documentElement.dataset.theme = systemStatus.value.theme || 'dark'
  } catch (exception: any) { error.value = exception.message }
}
async function refreshSpecial(field: Field) { if (field.data_endpoint && field.widget !== 'interception_stone_import') field.special_data = await api.get(field.data_endpoint) }
async function loadWorkspace() {
  if (!selectedName.value) return
  try {
    schema.value = await api.get(`/api/${selectedName.value}/schema`)
    const monitors = await api.get('/api/system/monitors').catch(() => [])
    allFields().forEach(field => { if (field.key.endsWith('.ScreenNumber')) field.options = monitors })
    Object.values(schema.value.tasks).forEach((task: any) => task.groups.forEach((group: any) => { if (group.collapsed) collapsed.value[group.key] = true }))
    await Promise.all(allFields().filter(field => field.data_endpoint && field.widget !== 'interception_stone_import').map(field => refreshSpecial(field).catch(() => null)))
    queue.value = await api.get(`/api/${selectedName.value}/queue`)
    error.value = ''
  } catch (exception: any) { error.value = exception.message }
}
function dashboard() { router.push('/') }
function enter(name: string) { router.push(`/i/${name}/overview`) }
function openTask(task: any, page: string) { router.push(`/i/${selectedName.value}/${page}/${task.key}`) }
function toggleRail(menu: any) { railCollapsed.value[menu.key] = !railCollapsed.value[menu.key] }
async function lifecycle(action: 'start' | 'stop', name = selectedName.value) { if (action === 'stop' && !confirm(`停止实例 ${name}？`)) return; try { await api.post(`/api/${name}/${action}`); await loadInstances() } catch (exception: any) { error.value = exception.message } }
async function saveValue(field: Field, value: any) {
  try { const result = await api.patch(`/api/${selectedName.value}/config`, { key: field.key, value }); if (!result.ok) throw new Error(result.message); field.value = result.applied[field.key]; saved.value[field.key] = true; setTimeout(() => delete saved.value[field.key], 1200) } catch (exception: any) { error.value = exception.message }
}
function save(field: Field, event: Event) { const input = event.target as HTMLInputElement; saveValue(field, field.widget === 'checkbox' ? input.checked : input.value) }
async function pickedPath(field: Field, path: string) {
  await saveValue(field, path)
  if (field.path_picker?.after_select !== 'autofill_game_path_from_launcher') return
  const gamePath = allFields().find(item => item.key === 'PCClient.PCClientInfo.GamePath')
  if (gamePath && !gamePath.value) {
    const separator = path.includes('\\') ? '\\' : '/'
    await saveValue(gamePath, `${path.replace(/[\\/][^\\/]+$/, '')}${separator}..${separator}NIKKE${separator}game${separator}nikke.exe`)
  }
}
async function importInterception(field: Field, path: string) {
  if (!field.data_endpoint) return
  importBusy.value[field.key] = true
  try { const result = await api.post(field.data_endpoint, { path }); if (!result.ok) throw new Error(result.message || '导入失败'); const chart = allFields().find(item => item.widget === 'interception_stone_charts'); if (chart) await refreshSpecial(chart); error.value = `已导入 ${result.imported || 0} 条，跳过 ${result.skipped || 0} 条。` } catch (exception: any) { error.value = exception.message } finally { delete importBusy.value[field.key] }
}
function toggleTheme() { const theme = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'; document.documentElement.dataset.theme = theme; api.post('/api/system/theme', { theme }).then(() => systemStatus.value.theme = theme).catch(exception => error.value = exception.message) }
async function setLanguage(event: Event) { try { const language = (event.target as HTMLSelectElement).value; await api.post('/api/system/language', { language }); await loadSystem(); await loadWorkspace() } catch (exception: any) { error.value = exception.message } }
async function createInstance() { const name = prompt('新实例名称')?.trim(); if (!name) return; const origin = prompt('复制来源实例', instances.value[0]?.name || 'template-nkas')?.trim() || 'template-nkas'; try { await api.post('/api/instances', { name, origin }); await loadInstances() } catch (exception: any) { error.value = exception.message } }
async function importInstance(event: Event) { const file = (event.target as HTMLInputElement).files?.[0]; if (!file) return; try { const response = await fetch('/api/instances/import', { method: 'POST', headers: { 'X-NKAS-Filename': file.name }, body: await file.arrayBuffer() }); const result = await response.json(); if (!response.ok) throw new Error(result.message); await loadInstances() } catch (exception: any) { error.value = exception.message } }
async function deleteInstance(name: string) { if (!confirm(`删除实例 ${name}？此操作不可恢复。`)) return; try { await api.del(`/api/${name}`); await loadInstances(); if (selectedName.value === name) dashboard() } catch (exception: any) { error.value = exception.message } }
async function dismissNotice(notice: any) { try { await api.post(`/api/system/notices/${notice.key}/dismiss`); notices.value = notices.value.filter(item => item.key !== notice.key) } catch (exception: any) { error.value = exception.message } }
function startSockets() {
  stateSocket?.close(); logSocket?.close(); queueSocket?.close()
  stateSocket = new JsonSocket('/ws/state', event => { const instance = instances.value.find(item => item.name === event.name); if (instance) instance.state = event.state })
  stateSocket.connect()
  if (!selectedName.value) return
  logSocket = new JsonSocket(`/ws/${selectedName.value}/log`, event => logs.value = [...logs.value.slice(-399), event.html])
  queueSocket = new JsonSocket(`/ws/${selectedName.value}/queue`, event => queue.value = event)
  logSocket.connect(); queueSocket.connect()
}
onMounted(async () => { await loadSystem(); await loadInstances(); await loadWorkspace(); startSockets() })
watch(() => route.fullPath, async () => { logs.value = []; fieldFilter.value = ''; await loadWorkspace(); startSockets() })
watch(logs, async () => { if (autoScroll.value) { await nextTick(); if (logBody.value) logBody.value.scrollTop = logBody.value.scrollHeight } })
onBeforeUnmount(() => { stateSocket?.close(); logSocket?.close(); queueSocket?.close() })
</script>

<template>
  <div class="app" :class="{ 'legacy-electron': legacyElectron }">
    <nav class="sidebar">
      <div class="brand"><div class="brand-logo">N</div><div><div class="brand-name">NKAS</div><div class="brand-sub">NIKKE AUTO</div></div></div>
      <div class="side-section"><button class="side-item" :class="{ active: isDashboard }" @click="dashboard"><span class="sicon">📊</span>全局总览</button></div>
      <div class="side-section"><div class="side-label">实例</div><button v-for="instance in instances" :key="instance.name" class="side-item" :class="{ active: selectedName === instance.name }" @click="enter(instance.name)"><span class="inst-avatar" :class="{ idle: instance.state !== 1 }">{{ initials(instance.name) }}<span class="ring" :class="stateClass(instance.state)"></span></span>{{ instance.name }}<span class="badge" :class="{ 'idle-badge': instance.state !== 1 }">{{ instance.state === 1 ? '运行中' : '空闲' }}</span></button><button class="side-item" @click="router.push('/manage')"><span class="sicon">＋</span>新建实例</button></div>
      <div class="side-section"><div class="side-label">系统</div><button class="side-item" :class="{ active: isManage }" @click="router.push('/manage')">🗂 实例管理</button><button class="side-item" :class="{ active: isSettings }" @click="router.push('/settings')">⚙️ 设置 / 更新</button><button class="side-item" :class="{ active: isAbout }" @click="router.push('/about')">ℹ️ 关于</button></div><div class="side-spacer"></div><div class="side-footer"><button class="icon-btn" @click="toggleTheme">🌙 主题</button><select class="icon-btn" :value="systemStatus.language" @change="setLanguage"><option value="zh-CN">简体中文</option><option value="en-US">English</option><option value="ja-JP">日本語</option></select></div>
    </nav>
    <aside v-if="isWorkspace" class="rail"><div class="rail-head"><div class="rail-inst"><span class="inst-avatar" :class="{ idle: selectedInstance?.state !== 1 }">{{ initials(selectedName) }}<span class="ring" :class="stateClass(selectedInstance?.state)"></span></span><div><div class="rail-inst-name">{{ selectedName }}</div><div class="rail-inst-state">{{ stateText(selectedInstance?.state) }}</div></div></div><label class="rail-search">🔍 <input v-model="taskFilter" placeholder="筛选任务…"></label></div><div class="rail-list"><button class="rail-item" :class="{ active: selectedPage === 'overview' }" @click="router.push(`/i/${selectedName}/overview`)">📈 调度总览</button><template v-for="menu in visibleMenus" :key="menu.key"><button class="rail-group" :class="{ expanded: !railCollapsed[menu.key] || taskFilter }" @click="toggleRail(menu)"><span class="chev">›</span>{{ menu.name }}<span class="rail-count">{{ menu.tasks.filter((task: any) => taskEnabled(task.key)).length }}/{{ menu.tasks.length }}</span></button><div v-show="!railCollapsed[menu.key] || taskFilter" class="rail-tasks"><button v-for="task in menu.tasks" :key="task.key" class="rail-item" :class="{ active: selectedTask === task.key }" @click="openTask(task, menu.page === 'tool' ? 'tool' : 'task')"><span>{{ menu.page === 'tool' ? '🛠' : '•' }}</span>{{ task.name }}<span v-if="selectedInstance?.current_task === task.key" class="spin"></span><span v-else class="mini-dot" :class="taskEnabled(task.key) ? 'on' : 'off'"></span></button></div></template></div></aside>
    <main class="main"><header class="topbar"><div class="crumb"><span v-if="isWorkspace" class="pre">{{ selectedName }} /</span><span class="cur">{{ pageTitle() }}</span></div><span v-if="isWorkspace" class="status-pill" :class="stateClass(selectedInstance?.state)">{{ stateText(selectedInstance?.state) }}</span><div class="topbar-right"><span v-if="error" class="sub">{{ error }}</span></div></header>
      <div v-if="notices.length" class="notice-stack"><article v-for="notice in notices" :key="notice.key" class="notice-card" :class="notice.type"><div><strong>{{ notice.data.title || '系统通知' }}</strong><p>{{ notice.data.content || notice.data.error || notice.data.messages?.join(' · ') || '有新的系统通知。' }}</p></div><button class="btn sm" @click="dismissNotice(notice)">知道了</button></article></div>
      <section v-if="isDashboard" class="view"><div class="stat-row"><article class="card stat-card"><div class="stat-icon">🖥️</div><div><div class="stat-num">{{ instances.length }}</div><div class="stat-lbl">实例总数</div></div></article><article class="card stat-card"><div class="stat-icon green">▶️</div><div><div class="stat-num">{{ runningCount }}</div><div class="stat-lbl">运行中</div></div></article></div><div class="section-title">实例</div><div class="inst-grid"><article v-for="instance in instances" :key="instance.name" class="card inst-card" :class="{ 'is-running': instance.state === 1 }"><div class="inst-card-head"><span class="inst-avatar">{{ initials(instance.name) }}</span><div><h3>{{ instance.name }}</h3><div class="sub">mod: {{ instance.mod }}</div></div></div><div class="inst-now"><span>当前任务</span><span>{{ instance.current_task || '无' }}</span></div><div class="inst-now"><span>下一任务</span><span>{{ instance.next_task || '—' }}</span></div><div class="inst-card-foot"><button class="btn sm" @click="lifecycle(instance.state === 1 ? 'stop' : 'start', instance.name)">{{ instance.state === 1 ? '停止' : '启动' }}</button><button class="btn primary sm" @click="enter(instance.name)">进入 →</button></div></article><button class="card add-card" @click="createInstance">＋ 新建实例</button></div></section>
      <section v-else-if="isWorkspace && selectedPage === 'overview'" class="view"><div class="ov-layout"><div class="ov-left"><article class="card hero-sched"><div style="flex:1"><b>调度器</b><div class="sub">{{ selectedInstance?.current_task || selectedInstance?.next_task || '等待任务队列' }}</div></div><button class="btn" @click="lifecycle(selectedInstance?.state === 1 ? 'stop' : 'start')">{{ selectedInstance?.state === 1 ? '停止' : '启动' }}</button></article><article class="card queue-card"><div class="queue-group-label">任务队列</div><div class="timeline"><div v-for="item in queue.running" :key="item.command" class="tl-item running">{{ item.name_i18n }}<span class="t">进行中</span></div><div v-for="item in [...queue.pending, ...queue.waiting]" :key="item.command" class="tl-item">{{ item.name_i18n }}<span class="t">{{ item.next_run }}</span></div></div></article></div><article class="card log-card"><div class="log-head"><b>实时日志</b><span class="note">WebSocket 推送</span><label class="log-autoscroll"><input v-model="autoScroll" type="checkbox"> 自动滚动</label></div><div ref="logBody" class="log-body"><div v-for="(line, index) in logs" :key="index" v-html="line"></div></div></article></div></section>
      <section v-else-if="isWorkspace" class="view"><div class="task-layout"><div><article class="card task-hero"><div class="task-icon">{{ selectedPage === 'tool' ? '🛠' : '⚙️' }}</div><div style="flex:1"><h2>{{ taskSchema?.name || selectedTask }}</h2><div class="sub">{{ taskSchema?.help || '保存后立即生效' }}</div></div><button class="btn primary" @click="api.post(selectedPage === 'tool' ? `/api/${selectedName}/tool/${selectedTask}/start` : `/api/${selectedName}/task/${selectedTask}/run`).catch(exception => error = exception.message)">{{ selectedPage === 'tool' ? '▶ 启动' : '▶ 立即运行' }}</button></article><label class="field-search">🔍 <input v-model="fieldFilter" placeholder="搜索本任务配置…"></label><div class="cfg-groups"><article v-for="group in taskSchema?.groups || []" :id="groupId(group)" :key="group.key" class="card group-card" :class="{ collapsed: collapsed[group.key] }"><button class="group-head" @click="collapsed[group.key] = !collapsed[group.key]"><h4>{{ group.name }}</h4><span v-if="group.key === 'Scheduler'" class="group-summary">{{ group.fields.find((field: Field) => field.key.endsWith('.Enable'))?.value ? '已启用' : '未启用' }}</span><span class="group-summary">{{ collapsed[group.key] ? '▸' : '▾' }}</span></button><div class="group-body"><div v-for="field in groupFields(group)" :key="field.key" class="field"><div class="field-label"><div class="fname">{{ field.title }}</div><div v-if="field.help" class="fhelp">{{ field.help }}</div></div><div class="field-control"><label v-if="field.widget === 'checkbox'" class="switch"><input type="checkbox" :checked="field.value" :disabled="field.display !== 'show'" @change="save(field, $event)"><span class="slider"></span></label><select v-else-if="field.widget === 'select'" :value="field.value" :disabled="field.display !== 'show'" @change="save(field, $event)"><option v-for="option in field.options" :key="option.value || option" :value="option.value || option">{{ option.label || option }}</option></select><FieldPathPicker v-else-if="field.path_picker" :value="field.value" :picker="field.path_picker" :disabled="field.display !== 'show'" @picked="pickedPath(field, $event)" @error="error = $event"/><textarea v-else-if="field.widget === 'textarea'" :value="field.value" :readonly="field.display !== 'show'" @change="save(field, $event)"></textarea><FieldStorage v-else-if="field.widget === 'storage'" :value="field.value" :disabled="field.display !== 'show'" @clear="saveValue(field, {})"/><FieldItemTable v-else-if="field.widget === 'item_table'" :data="field.special_data" :loading="!field.special_data"/><FieldInterception v-else-if="field.widget === 'interception_stone_import'" :widget="field.widget" :busy="Boolean(importBusy[field.key])" @import="importInterception(field, $event)" @error="error = $event"/><FieldInterception v-else-if="field.widget === 'interception_stone_charts'" :widget="field.widget" :data="field.special_data"/><input v-else :type="field.widget === 'datetime' ? 'datetime-local' : field.key.endsWith('.Password') ? 'password' : 'text'" :value="field.value" :readonly="field.display !== 'show'" @change="save(field, $event)"><span v-if="saved[field.key]" class="saved">✓ 已保存</span></div></div><div v-if="!groupFields(group).length" class="special-empty">没有匹配的配置项。</div></div></article></div></div><aside class="card anchor-nav"><div class="side-label">本页分组</div><button v-for="group in taskSchema?.groups || []" :key="group.key" class="anchor-nav-item" @click="jumpToGroup(group)">{{ group.name }}</button></aside></div></section>
      <section v-else-if="isManage" class="view"><div style="display:flex;gap:10px;margin-bottom:18px"><button class="btn primary" @click="createInstance">＋ 新建实例</button><label class="btn">⤒ 导入配置<input type="file" accept=".json" hidden @change="importInstance"></label></div><article class="card" style="overflow:hidden"><table><thead><tr><th>名称</th><th>Mod</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="instance in instances" :key="instance.name"><td>{{ instance.name }}</td><td>{{ instance.mod }}</td><td>{{ stateText(instance.state) }}</td><td><button class="btn sm" @click="enter(instance.name)">进入</button> <a class="btn sm" :href="`/api/${instance.name}/export`">导出</a> <button class="btn danger sm" :disabled="instance.state === 1" @click="deleteInstance(instance.name)">删除</button></td></tr></tbody></table></article></section>
      <section v-else-if="isSettings" class="view"><div class="cfg-groups"><article class="card group-card"><div class="group-head"><h4>应用更新</h4></div><div class="group-body"><div class="field"><div class="field-label"><div class="fname">当前版本</div></div><div class="field-control">{{ systemStatus.version }}</div></div><div class="field"><div class="field-label"><div class="fname">更新</div></div><div class="field-control"><button class="btn primary sm" @click="api.post('/api/update').catch(exception => error = exception.message)">检查更新</button><button class="btn sm danger" @click="api.post('/api/restart').catch(exception => error = exception.message)">强制重启</button></div></div><div v-for="commit in updateInfo.history || []" :key="commit[0]" class="history-row"><code>{{ commit[0] }}</code><span>{{ commit[3] }}</span></div></div></article><article class="card group-card"><div class="group-head"><h4>界面</h4></div><div class="group-body"><button class="btn sm" @click="toggleTheme">切换主题</button></div></article><article class="card group-card"><div class="group-head"><h4>远程访问</h4></div><div class="group-body">{{ remoteInfo.entry_point || '未启用远程访问' }}</div></article></div></section>
      <section v-else class="view"><article class="card about-panel"><h2>关于 NKAS</h2><p>NKAS 是免费开源软件，付费购买请退款。</p><p><a href="https://github.com/megumiss/NIKKEAutoScript" target="_blank">github.com/megumiss/NIKKEAutoScript</a></p></article></section>
    </main>
  </div>
</template>
