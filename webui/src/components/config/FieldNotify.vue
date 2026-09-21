<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import AppIcon from '../AppIcon.vue'
import AppSelect from '../AppSelect.vue'
import LinkifiedText from '../LinkifiedText.vue'
import { highlightTextarea, onTextareaInput, vAutosize } from '../../composables/useTextarea'
import { t } from '../../i18n'
import type { Field } from '../../types'

// 通知渠道组件：按后端下发的渠道定义渲染表单（渠道下拉 + 该渠道的参数字段），
// 保存时写回 OnePushConfig 的 YAML 文本——存储格式与运行时链路都不变。
// 需要填写表单未覆盖的字段（或粘贴旧配置）时切到 YAML 模式直接编辑。
const props = defineProps<{ field: Field; data?: any; disabled?: boolean; busy?: boolean; scope?: string }>()
const emit = defineEmits<{
  save: [payload: { provider: string; params: Record<string, any> }]
  'save-raw': [value: string]
  test: []
}>()

const mode = ref<'form' | 'raw'>('form')
const provider = ref('')
const params = ref<Record<string, any>>({})
const reveal = ref<Record<string, boolean>>({})
const rawValue = ref('')
// 切换渠道时暂存已填内容，切回来不用重新输入（只保存当前渠道的字段）。
// 存在 localStorage 里是为了刷新页面后仍在——否则误点一次渠道下拉，
// 上一个渠道的密钥就被写没了。
const DRAFT_PREFIX = 'nkas.notify.drafts.'
const drafts = ref<Record<string, Record<string, any>>>({})
function loadDrafts() {
  try {
    drafts.value = JSON.parse(localStorage.getItem(DRAFT_PREFIX + (props.scope || '')) || '{}')
  } catch { drafts.value = {} }
}
function saveDrafts() {
  try { localStorage.setItem(DRAFT_PREFIX + (props.scope || ''), JSON.stringify(drafts.value)) } catch { /* 隐私模式下忽略 */ }
}

const plan = computed(() => props.data || {})
const providers = computed<any[]>(() => plan.value.providers || [])
const extra = computed<string[]>(() => plan.value.extra || [])
const current = computed<any>(() => providers.value.find(item => item.id === provider.value) || null)
const providerOptions = computed(() => [
  { value: '', label: t('不启用推送') },
  ...providers.value.map(item => ({ value: item.id, label: item.label })),
])
// 渠道的所有参数（必填 + 可选）一次铺开，不再折叠
const allParams = computed<any[]>(() => current.value?.params || [])
const readonly = computed(() => Boolean(props.disabled))

function declaredKeys(id: string) {
  return new Set<string>((providers.value.find(item => item.id === id)?.params || []).map((item: any) => item.key))
}
function extraValues() {
  const config = plan.value.config || {}
  return Object.fromEntries(extra.value.map((key: string) => [key, config[key]]))
}
function applyPlan() {
  const next = String(plan.value.provider || '')
  provider.value = next
  const declared = declaredKeys(next)
  const config = plan.value.config || {}
  params.value = Object.fromEntries(
    Object.entries(config).filter(([key]) => key !== 'provider' && (declared.has(key) || extra.value.includes(key))),
  )
  rememberCurrent()
}

watch(() => props.data, () => applyPlan(), { immediate: true, deep: true })
watch(() => props.scope, () => loadDrafts(), { immediate: true })

function submit() {
  emit('save', { provider: provider.value, params: { ...params.value } })
}
// 当前渠道已填内容随时记进草稿，刷新页面或切走再切回都还在
function rememberCurrent() {
  const declared = declaredKeys(provider.value)
  drafts.value[provider.value] = Object.fromEntries(
    Object.entries(params.value).filter(([key]) => declared.has(key)),
  )
  saveDrafts()
}
function chooseProvider(id: string) {
  if (id === provider.value) return
  provider.value = id
  // 只带当前渠道声明的字段与本表单不认识的额外字段，避免残留上一个渠道的参数
  const declared = declaredKeys(id)
  params.value = Object.fromEntries(
    Object.entries({ ...extraValues(), ...(drafts.value[id] || {}) })
      .filter(([key]) => declared.has(key) || extra.value.includes(key)),
  )
  submit()
}
function setParam(key: string, value: any) {
  params.value[key] = value
  rememberCurrent()
  submit()
}
function toggleMode() {
  if (mode.value === 'form') rawValue.value = String(props.field.value ?? '')
  mode.value = mode.value === 'form' ? 'raw' : 'form'
}
function onRawInput(event: Event) {
  rawValue.value = (event.target as HTMLTextAreaElement).value
  // 复用结构化 textarea 的实时高亮：把编辑内容同步回字段模型
  onTextareaInput(props.field, event)
}
function onRawChange() { emit('save-raw', rawValue.value) }
</script>

<template>
  <div :id="`field-${field.key}`" class="field">
    <div class="field-label">
      <div class="fname">{{ field.title }}</div>
      <div v-if="field.help" class="fhelp"><LinkifiedText :text="field.help" /></div>
    </div>
    <div class="field-control">
      <AppSelect :model-value="provider" :options="providerOptions" :disabled="readonly" @change="chooseProvider"/>
    </div>
  </div>

  <div class="notify-field">
    <template v-if="mode === 'form'">
      <div v-if="plan.error" class="notify-notice">{{ t('当前配置不是合法的 YAML，请切到 YAML 模式修正后再保存。') }}</div>
      <div v-else-if="!provider" class="notify-notice">{{ t('未选择推送渠道：只发送系统通知，不会推送外部消息。') }}</div>
      <template v-else>
        <div class="notify-params">
          <div v-for="param in allParams" :key="param.key" class="notify-param">
            <div class="notify-label">{{ param.label }}<b v-if="param.required" class="notify-required">*</b></div>
            <div class="notify-control">
              <AppSelect v-if="param.type === 'select'" :model-value="params[param.key] ?? ''" :options="param.options" :disabled="readonly" @change="(value: any) => setParam(param.key, value)"/>
              <label v-else-if="param.type === 'bool'" class="switch sm"><input type="checkbox" :checked="Boolean(params[param.key])" :disabled="readonly" @change="setParam(param.key, ($event.target as HTMLInputElement).checked)"><span class="slider"></span></label>
              <div v-else-if="param.type === 'password'" class="notify-secret">
                <input :type="reveal[param.key] ? 'text' : 'password'" :value="params[param.key] ?? ''" :placeholder="param.placeholder" :disabled="readonly" autocomplete="off" spellcheck="false" @change="setParam(param.key, ($event.target as HTMLInputElement).value)">
                <button type="button" class="btn" @click="reveal[param.key] = !reveal[param.key]">{{ reveal[param.key] ? t('隐藏') : t('显示') }}</button>
              </div>
              <textarea v-else-if="param.type === 'json'" class="notify-json" rows="3" :value="params[param.key] ?? ''" :placeholder="param.placeholder" :disabled="readonly" spellcheck="false" @change="setParam(param.key, ($event.target as HTMLTextAreaElement).value)"></textarea>
              <input v-else :type="param.type === 'number' ? 'number' : 'text'" :value="params[param.key] ?? ''" :placeholder="param.placeholder" :disabled="readonly" @change="setParam(param.key, ($event.target as HTMLInputElement).value)">
            </div>
            <div v-if="param.help" class="notify-help">{{ param.help }}</div>
          </div>
        </div>

        <div v-if="extra.length" class="notify-notice">
          {{ t('以下字段不在表单中，保存时原样保留：') }}<code>{{ extra.join('、') }}</code>
        </div>
      </template>
    </template>

    <div v-else class="code-wrap code-wrap-resizable">
      <pre class="code-highlight" v-html="highlightTextarea(field)"></pre>
      <textarea v-autosize class="code-input" :value="rawValue" :readonly="readonly" spellcheck="false" @input="onRawInput" @change="onRawChange"></textarea>
    </div>

    <div class="notify-foot">
      <button type="button" class="btn" :disabled="readonly || busy" @click="emit('test')"><AppIcon name="message" :size="14" color="currentColor"/> {{ busy ? t('发送中…') : t('测试通知') }}</button>
      <button type="button" class="btn notify-mode" @click="toggleMode">{{ mode === 'form' ? t('YAML 模式') : t('表单模式') }}</button>
    </div>
  </div>
</template>
