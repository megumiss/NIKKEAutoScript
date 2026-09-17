<script setup lang="ts">
// 本地内联 SVG 图标（取自 reicon.dev，MIT License），避免引入额外运行时依赖。
// 新增图标：把对应 SVG 放进 webui/src/assets/icons/ 即可按文件名引用。
import { computed } from 'vue'

const props = withDefaults(defineProps<{ name: string; size?: number; color?: string }>(), { size: 18 })

// 图标语义配色（reicon 图标名 -> 颜色）。未列出的图标继承父级文字色
// （check/x/arrow-right/monitor/play 等按钮、toast、彩色背景块内图标刻意不映射）。
const ICON_COLORS: Record<string, string> = {
  'chart-square': '#0099ff',
  layers: '#6a4cf5',
  box: '#ff7a3d',
  'file-text': '#22c55e',
  'square-top-up': '#ff5577',
  'info-circle': '#0099ff',
  designtools: '#d44df0',
  globe: '#6a4cf5',
  plus: '#0099ff',
  'trend-up': '#0099ff',
  calendar: '#ff7a3d',
  gear: '#999999',
  gamepad: '#6a4cf5',
  timer: '#0099ff',
  lightbulb: '#ff7a3d',
  message: '#0099ff',
  'terminal-square': '#0099ff',
  import: '#0099ff',
  refresh: '#0099ff',
  sun: '#ff7a3d',
  moon: '#6a4cf5',
  'alert-triangle': '#ff5577',
  rocket: '#6a4cf5',
  download: '#0099ff',
  gift: '#d44df0',
  book: '#ff7a3d',
  bank: '#ff7a3d',
  building: '#999999',
  coffee: '#ff7a3d',
  grid: '#6a4cf5',
  map: '#22c55e',
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
  <span class="app-icon" :data-name="name" :style="{ width: px, height: px, color: color || undefined }" aria-hidden="true" v-html="svg"></span>
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
