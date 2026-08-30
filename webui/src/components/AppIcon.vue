<script setup lang="ts">
// 本地内联 SVG 图标（取自 reicon.dev，MIT License），避免引入额外运行时依赖。
// 新增图标：把对应 SVG 放进 webui/src/assets/icons/ 即可按文件名引用。
import { computed } from 'vue'

const props = withDefaults(defineProps<{ name: string; size?: number; color?: string }>(), { size: 18 })

// 图标语义配色（reicon 图标名 -> 颜色）。未列出的图标继承父级文字色
// （check/x/arrow-right/monitor/play 等按钮、toast、彩色背景块内图标刻意不映射）。
const ICON_COLORS: Record<string, string> = {
  'chart-square': '#3b82f6',
  layers: '#8b5cf6',
  box: '#f59e0b',
  'file-text': '#10b981',
  'square-top-up': '#ef4444',
  'info-circle': '#06b6d4',
  designtools: '#ec4899',
  globe: '#6366f1',
  plus: '#3b82f6',
  'trend-up': '#3b82f6',
  calendar: '#f59e0b',
  gear: '#94a3b8',
  gamepad: '#8b5cf6',
  timer: '#06b6d4',
  lightbulb: '#f59e0b',
  message: '#3b82f6',
  'terminal-square': '#0ea5e9',
  import: '#3b82f6',
  refresh: '#06b6d4',
  sun: '#f59e0b',
  moon: '#6366f1',
  'alert-triangle': '#ef4444',
  rocket: '#8b5cf6',
  download: '#3b82f6',
  gift: '#ec4899',
  book: '#f59e0b',
  bank: '#f59e0b',
  building: '#94a3b8',
  coffee: '#d97757',
  grid: '#6366f1',
  map: '#10b981',
}

const modules = import.meta.glob('../assets/icons/*.svg', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const svg = computed(() => modules[`../assets/icons/${props.name}.svg`] ?? '')
const px = computed(() => `${props.size}px`)
const color = computed(() => props.color ?? ICON_COLORS[props.name] ?? '')
</script>

<template>
  <span class="app-icon" :style="{ width: px, height: px, color: color || undefined }" aria-hidden="true" v-html="svg"></span>
</template>

<style scoped>
.app-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex: none;
  line-height: 0;
  vertical-align: -0.15em;
}
.app-icon :deep(svg) {
  width: 100%;
  height: 100%;
  display: block;
}
</style>
