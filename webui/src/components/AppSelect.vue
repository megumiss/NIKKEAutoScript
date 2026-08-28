<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const props = defineProps<{
  modelValue: any
  options: any[]
  disabled?: boolean
  placeholder?: string
}>()
const emit = defineEmits(['update:modelValue', 'change', 'open'])
const open = ref(false)
const root = ref<HTMLElement>()
const pop = ref<HTMLElement>()
const popStyle = ref<Record<string, string>>({})

function valueOf(option: any) { return option !== null && typeof option === 'object' ? option.value ?? option : option }
function labelOf(option: any) { return option !== null && typeof option === 'object' ? option.label ?? String(option.value ?? '') : String(option) }
const currentLabel = computed(() => {
  const hit = (props.options || []).find(option => valueOf(option) === props.modelValue)
  if (hit) return labelOf(hit)
  return props.modelValue === undefined || props.modelValue === null || props.modelValue === '' ? (props.placeholder || '') : String(props.modelValue)
})

function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (!open.value || !root.value) return
  emit('open')
  // The popup is teleported to body so it can escape overflow:hidden cards;
  // position it under (or above) the button in viewport coordinates.
  const rect = root.value.getBoundingClientRect()
  const flip = rect.bottom + 270 > window.innerHeight && rect.top > 300
  popStyle.value = flip
    ? { left: `${rect.left}px`, width: `${rect.width}px`, bottom: `${window.innerHeight - rect.top + 6}px` }
    : { left: `${rect.left}px`, width: `${rect.width}px`, top: `${rect.bottom + 6}px` }
}
function choose(option: any) {
  emit('update:modelValue', valueOf(option))
  emit('change', valueOf(option))
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
  <div ref="root" class="app-select" :class="{ open, disabled }">
    <button type="button" class="app-select-btn" :disabled="disabled" @click="toggle">
      <span class="app-select-label">{{ currentLabel }}</span>
      <span class="app-select-arrow">›</span>
    </button>
    <Teleport to="body">
      <div v-if="open" ref="pop" class="app-select-pop" :style="popStyle">
        <button v-for="(option, index) in options" :key="index" type="button" class="app-select-option" :class="{ active: valueOf(option) === modelValue }" @click="choose(option)">
          <span class="app-select-option-label">{{ labelOf(option) }}</span>
          <span v-if="valueOf(option) === modelValue" class="app-select-check">✓</span>
        </button>
        <div v-if="!options?.length" class="app-select-empty">—</div>
      </div>
    </Teleport>
  </div>
</template>
