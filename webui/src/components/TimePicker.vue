<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'

// 主题化时间选择器：替代原生 input[type=time]，其弹层是浏览器控件无法定制样式。
// 结构/交互对齐 AppSelect：按钮 + teleport 到 body 的浮层，点外/Esc 关闭。
const props = defineProps<{
  modelValue?: string
  disabled?: boolean
  placeholder?: string
}>()
const emit = defineEmits(['update:modelValue', 'change'])

const open = ref(false)
const root = ref<HTMLElement>()
const pop = ref<HTMLElement>()
const popStyle = ref<Record<string, string>>({})
// 浮层里待提交的 hour（点分钟时才提交，避免只选小时就改值）
const pendingHour = ref<number | null>(null)

const HOURS = Array.from({ length: 24 }, (_, i) => i)
const MINUTES = Array.from({ length: 60 }, (_, i) => i)
const pad = (n: number) => String(n).padStart(2, '0')

const currentHour = computed(() => {
  const match = /^(\d{1,2}):/.exec(props.modelValue || '')
  return match ? Number(match[1]) : null
})
const currentMinute = computed(() => {
  const match = /:(\d{1,2})$/.exec(props.modelValue || '')
  return match ? Number(match[1]) : null
})
const activeHour = computed(() => pendingHour.value ?? currentHour.value)

async function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (!open.value || !root.value) return
  pendingHour.value = currentHour.value
  // 与 AppSelect 一致：fixed 定位到按钮下方，空间不足时翻转到上方
  const rect = root.value.getBoundingClientRect()
  const flip = rect.bottom + 270 > window.innerHeight && rect.top > 300
  popStyle.value = flip
    ? { left: `${rect.left}px`, bottom: `${window.innerHeight - rect.top + 6}px` }
    : { left: `${rect.left}px`, top: `${rect.bottom + 6}px` }
  await nextTick()
  // 手动 scrollTop 定位到当前值，避免 scrollIntoView 带动外层页面滚动
  pop.value?.querySelectorAll<HTMLElement>('.tp-col').forEach(col => {
    const cell = col.querySelector<HTMLElement>('.tp-cell.on')
    if (cell) col.scrollTop = cell.offsetTop - col.clientHeight / 2 + cell.clientHeight / 2
  })
}
function pickHour(h: number) { pendingHour.value = h }
function pickMinute(m: number) {
  const value = `${pad(activeHour.value ?? 0)}:${pad(m)}`
  emit('update:modelValue', value)
  emit('change', value)
  open.value = false
}
function onDocClick(event: MouseEvent) {
  const target = event.target as Node
  if (root.value?.contains(target) || pop.value?.contains(target)) return
  open.value = false
}
function onKeydown(event: KeyboardEvent) { if (event.key === 'Escape') open.value = false }
onMounted(() => { document.addEventListener('click', onDocClick); document.addEventListener('keydown', onKeydown) })
onBeforeUnmount(() => { document.removeEventListener('click', onDocClick); document.removeEventListener('keydown', onKeydown) })
</script>

<template>
  <div ref="root" class="tp" :class="{ open, disabled }">
    <button type="button" class="tp-btn" :disabled="disabled" @click="toggle">
      <span class="tp-value" :class="{ empty: !modelValue }">{{ modelValue || placeholder || '--:--' }}</span>
      <span class="tp-arrow">›</span>
    </button>
    <Teleport to="body">
      <div v-if="open" ref="pop" class="tp-pop" :style="popStyle">
        <div class="tp-col">
          <button v-for="h in HOURS" :key="h" type="button" class="tp-cell" :class="{ on: h === activeHour }" @click="pickHour(h)">{{ pad(h) }}</button>
        </div>
        <span class="tp-sep">:</span>
        <div class="tp-col">
          <button v-for="m in MINUTES" :key="m" type="button" class="tp-cell" :class="{ on: m === currentMinute && activeHour === currentHour }" @click="pickMinute(m)">{{ pad(m) }}</button>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<!-- 浮层 teleport 到 body，scoped 样式够不到；用 .tp- 前缀全局样式（同 AppSelect 走 base.css 的思路） -->
<style>
.tp { position: relative; display: inline-block; width: 104px; }
.tp-btn { display: flex; gap: 6px; align-items: center; width: 100%; height: 30px; padding: 0 9px; border: 1px solid var(--border); border-radius: 8px; color: var(--text); background: var(--card-2); font-size: 12.5px; transition: border-color .15s, box-shadow .15s; }
.tp-btn:hover { border-color: var(--border-light); }
.tp.open .tp-btn { border-color: var(--accent); box-shadow: 0 0 0 3px var(--accent-soft); }
.tp.disabled { opacity: .55; }
.tp-btn:disabled { cursor: not-allowed; }
.tp-value { flex: 1; overflow: hidden; text-align: left; white-space: nowrap; }
.tp-value.empty { color: var(--text-3); }
.tp-arrow { color: var(--text-3); font-size: 13px; transform: rotate(90deg); transition: transform .15s; }
.tp.open .tp-arrow { transform: rotate(-90deg); }
.tp-pop { position: fixed; z-index: 1000; display: flex; gap: 4px; align-items: stretch; padding: 6px; border: 1px solid var(--border); border-radius: 10px; background: var(--card); box-shadow: var(--shadow-hover); animation: rise .15s ease both; }
.tp-col { width: 58px; max-height: 216px; overflow-y: auto; }
.tp-sep { align-self: center; color: var(--text-3); }
.tp-cell { display: block; width: 100%; padding: 5px 0; border: 0; border-radius: 6px; color: var(--text-2); background: transparent; text-align: center; font-size: 12.5px; font-variant-numeric: tabular-nums; transition: background .12s; }
.tp-cell:hover { color: var(--text); background: var(--card-2); }
.tp-cell.on { color: var(--accent); background: var(--accent-soft); font-weight: 700; }
</style>
