<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import ScreenPreviewContent from './ScreenPreviewContent.vue'
import { t } from '../i18n'

const props = defineProps<{ name: string; language: string; index: number }>()
const emit = defineEmits<{ (e: 'close'): void; (e: 'activate'): void }>()
const headerEl = ref<HTMLElement>()
const windowEl = ref<HTMLElement>()
const compact = ref(true)
const MARGIN = 8
const SCREEN_ASPECT = 720 / 1280
const HEADER_HEIGHT = 40
const BORDER_SIZE = 2
const COMPACT_WIDTH = 242
const rect = reactive({ x: 0, y: 0, width: COMPACT_WIDTH, height: 0 })
const windowStyle = computed(() => ({ left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.width}px`, height: `${rect.height}px`, '--preview-header-height': `${HEADER_HEIGHT}px` }))
const sizeTitle = computed(() => compact.value ? t('放大预览') : t('缩为小窗'))
let gesture: { id: number; target: HTMLElement; resize: boolean; x: number; y: number; rect: typeof rect } | undefined

function setSize(width: number, maxWidth = window.innerWidth - MARGIN * 2, maxHeight = window.innerHeight - MARGIN * 2) {
  // Only the picture is 9:16; the toolbar and outer border are outside that ratio.
  const widthLimit = Math.max(BORDER_SIZE, Math.min(maxWidth, (maxHeight - HEADER_HEIGHT - BORDER_SIZE) * SCREEN_ASPECT + BORDER_SIZE))
  rect.width = Math.min(widthLimit, Math.max(220, width))
  rect.height = (rect.width - BORDER_SIZE) / SCREEN_ASPECT + HEADER_HEIGHT + BORDER_SIZE
}

function clampToViewport() {
  setSize(rect.width)
  rect.x = Math.max(MARGIN, Math.min(rect.x, window.innerWidth - rect.width - MARGIN))
  rect.y = Math.max(MARGIN, Math.min(rect.y, window.innerHeight - rect.height - MARGIN))
}

function resizeTo(width: number) {
  setSize(width, window.innerWidth - rect.x - MARGIN, window.innerHeight - rect.y - MARGIN)
  compact.value = rect.width <= COMPACT_WIDTH
}

function toggleSize() {
  compact.value = !compact.value
  const right = rect.x + rect.width
  const bottom = rect.y + rect.height
  setSize(compact.value ? COMPACT_WIDTH : 480)
  rect.x = right - rect.width
  rect.y = bottom - rect.height
  clampToViewport()
}

function startGesture(event: PointerEvent, resize = false) {
  if (event.button !== 0 || gesture) return
  const origin = event.target as HTMLElement
  const head = origin.closest<HTMLElement>('.preview-head')
  if (!resize && (!head || origin.closest('button'))) return
  if (!resize && head!.scrollWidth > head!.clientWidth && !origin.closest('.floating-preview-header')) return
  event.preventDefault()
  const target = resize ? event.currentTarget as HTMLElement : headerEl.value!
  target.focus({ preventScroll: true })
  target.setPointerCapture(event.pointerId)
  gesture = { id: event.pointerId, target, resize, x: event.clientX, y: event.clientY, rect: { ...rect } }
}

function moveGesture(event: PointerEvent) {
  if (!gesture || event.pointerId !== gesture.id) return
  const dx = event.clientX - gesture.x
  const dy = event.clientY - gesture.y
  if (gesture.resize) {
    const widthDelta = Math.abs(dx) >= Math.abs(dy * SCREEN_ASPECT) ? dx : dy * SCREEN_ASPECT
    resizeTo(gesture.rect.width + widthDelta)
  } else {
    rect.x = gesture.rect.x + dx
    rect.y = gesture.rect.y + dy
    clampToViewport()
  }
}

function stopGesture(event?: PointerEvent) {
  if (!gesture || (event && event.pointerId !== gesture.id)) return
  const { target, id } = gesture
  gesture = undefined
  if (target.hasPointerCapture(id)) target.releasePointerCapture(id)
}

function onKeydown(event: KeyboardEvent, resize = false) {
  if (event.target !== event.currentTarget) return
  const direction: Record<string, [number, number]> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }
  const delta = direction[event.key]
  if (!delta) return
  event.preventDefault()
  const step = event.shiftKey ? 40 : 10
  if (resize) {
    resizeTo(rect.width + (delta[0] || delta[1] * SCREEN_ASPECT) * step)
  } else {
    rect.x += delta[0] * step
    rect.y += delta[1] * step
    clampToViewport()
  }
}

onMounted(() => {
  setSize(rect.width)
  // Lay out the first windows side by side; cascade only when the viewport is full.
  const columns = Math.max(1, Math.floor((window.innerWidth - 24) / (rect.width + 12)))
  rect.x = window.innerWidth - rect.width - 24 - (props.index % columns) * (rect.width + 12)
  rect.y = window.innerHeight - rect.height - 88 - Math.floor(props.index / columns) * 36
  clampToViewport()
  headerEl.value?.focus({ preventScroll: true })
  window.addEventListener('resize', clampToViewport)
})

onBeforeUnmount(() => {
  stopGesture()
  window.removeEventListener('resize', clampToViewport)
  if (windowEl.value?.contains(document.activeElement)) document.querySelector<HTMLElement>('.preview-open')?.focus()
})
</script>

<template>
  <section ref="windowEl" class="floating-preview" :class="{ compact }" :style="windowStyle" role="region"
    :aria-label="`${t('画面预览')} - ${name}`" :data-preview-name="name" @pointerdown.capture="emit('activate')" @focusin="emit('activate')"
    @pointerdown="startGesture($event)" @pointermove="moveGesture" @pointerup="stopGesture" @pointercancel="stopGesture" @lostpointercapture="stopGesture">
    <ScreenPreviewContent embedded :name="name" :language="language">
      <template #title>
        <div ref="headerEl" class="floating-preview-header" tabindex="0" :aria-label="`${name} · ${t('拖动标题栏或使用方向键移动')}`" :title="`${name} · ${t('拖动标题栏或使用方向键移动')}`" @keydown="onKeydown($event)">
          <b>{{ name }}</b>
        </div>
      </template>
      <template #window-actions>
        <button class="floating-preview-action" type="button" :aria-label="sizeTitle" :title="sizeTitle" @click="toggleSize">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path v-if="compact" d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" /><path v-else d="M3 8h5V3m13 5h-5V3M8 21v-5H3m13 5v-5h5" /></svg>
        </button>
        <button class="floating-preview-action" type="button" :aria-label="t('关闭')" :title="t('关闭')" @click="emit('close')"><AppIcon name="x" :size="16" /></button>
      </template>
    </ScreenPreviewContent>
    <button class="preview-resize-handle" type="button" :aria-label="t('拖动或使用方向键调整大小')" :title="t('拖动或使用方向键调整大小')"
      @pointerdown.stop="startGesture($event, true)" @keydown="onKeydown($event, true)">
      <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 18 10-10m-4 10 4-4" /></svg>
    </button>
  </section>
</template>

<style scoped>
.floating-preview { position:absolute; display:flex; flex-direction:column; overflow:hidden; pointer-events:auto; border:1px solid var(--border-light); border-radius:12px; background:var(--card); box-shadow:0 12px 36px rgba(0,0,0,.24); }
.floating-preview:focus-within { border-color:var(--accent); }
.floating-preview-header { display:flex; flex:1; min-width:32px; align-items:center; align-self:stretch; cursor:grab; touch-action:none; user-select:none; }
.floating-preview-header > b { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:12px; }
.floating-preview-action { display:grid; place-items:center; flex:none; width:24px; height:24px; padding:3px; border:0; border-radius:5px; color:var(--text-3); background:transparent; cursor:pointer; }
.floating-preview-action:hover { color:var(--text); background:var(--card-3); }
.floating-preview svg { width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:2; stroke-linecap:round; stroke-linejoin:round; }
.floating-preview-header:focus-visible, .floating-preview :deep(button:focus-visible) { outline:2px solid var(--accent); outline-offset:-2px; }
.preview-resize-handle { position:absolute; right:0; bottom:0; display:grid; place-items:center; width:24px; height:24px; padding:0; border:0; border-radius:8px 0 0; color:var(--text-3); background:var(--card); cursor:nwse-resize; touch-action:none; }
</style>
