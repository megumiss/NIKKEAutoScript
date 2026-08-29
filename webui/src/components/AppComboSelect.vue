<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import AppIcon from './AppIcon.vue'

// Editable dropdown (combobox) styled after AppSelect: the text is freely
// editable and committing (blur/Enter) emits change, while the toggle opens
// the option list and asks the parent to refresh it via the dropdown event.
const props = defineProps<{
  modelValue: any
  options: any[]
  disabled?: boolean
  placeholder?: string
  loading?: boolean
  loadingText?: string
  emptyText?: string
}>()
const emit = defineEmits(['update:modelValue', 'change', 'dropdown'])
const open = ref(false)
const root = ref<HTMLElement>()
const pop = ref<HTMLElement>()
const popStyle = ref<Record<string, string>>({})

function valueOf(option: any) { return option !== null && typeof option === 'object' ? option.value ?? option : option }
function labelOf(option: any) { return option !== null && typeof option === 'object' ? option.label ?? String(option.value ?? '') : String(option) }
const currentValue = computed(() => props.modelValue ?? '')

function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (!open.value || !root.value) return
  emit('dropdown')
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
function onInput(event: Event) { emit('update:modelValue', (event.target as HTMLInputElement).value) }
function onChange(event: Event) { emit('change', (event.target as HTMLInputElement).value) }
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
  <div ref="root" class="app-select app-combo" :class="{ open, disabled }">
    <input class="app-combo-input" type="text" :value="currentValue" :disabled="disabled" :placeholder="placeholder" spellcheck="false" @input="onInput" @change="onChange">
    <button type="button" class="app-combo-toggle" :disabled="disabled" @click="toggle">
      <span class="app-select-arrow">›</span>
    </button>
    <Teleport to="body">
      <div v-if="open" ref="pop" class="app-select-pop" :style="popStyle">
        <div v-if="loading" class="app-select-empty">{{ loadingText || '…' }}</div>
        <template v-else>
          <button v-for="(option, index) in options" :key="index" type="button" class="app-select-option" :class="{ active: valueOf(option) === modelValue }" @click="choose(option)">
            <span class="app-select-option-label">{{ labelOf(option) }}</span>
            <span v-if="valueOf(option) === modelValue" class="app-select-check"><AppIcon name="check" :size="14" /></span>
          </button>
          <div v-if="!options?.length" class="app-select-empty">{{ emptyText || '—' }}</div>
        </template>
      </div>
    </Teleport>
  </div>
</template>
