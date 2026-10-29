<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import ScreenPreviewContent from './ScreenPreviewContent.vue'
import { t } from '../i18n'
import { useToastStore } from '../stores/toast'

const props = withDefaults(defineProps<{ name: string; language: string; floating?: boolean }>(), { floating: false })
const toast = useToastStore()
const expanded = ref(false)
const opening = ref(false)
const triggerEl = ref<HTMLButtonElement>()
const isTauri = Boolean((window as any).__TAURI_INTERNALS__)
let previousOverflow = ''

async function openPreview() {
  if (opening.value) return
  if (!isTauri) {
    expanded.value = true
    return
  }
  opening.value = true
  try {
    await (window as any).__TAURI__.core.invoke('open_preview_window', { name: props.name, language: props.language })
  } catch (exception: any) {
    toast.error = exception?.message || String(exception)
  } finally {
    opening.value = false
  }
}

function closePreview() {
  expanded.value = false
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') closePreview()
}

watch(expanded, async open => {
  document.removeEventListener('keydown', onKeydown)
  if (open) {
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', onKeydown)
    await nextTick()
    document.querySelector<HTMLElement>('.preview-modal-close')?.focus()
  } else {
    document.body.style.overflow = previousOverflow
    triggerEl.value?.focus()
  }
})

onBeforeUnmount(() => {
  document.body.style.overflow = previousOverflow
  document.removeEventListener('keydown', onKeydown)
})
</script>

<template>
  <button ref="triggerEl" :class="floating ? 'preview-open preview-fab' : 'btn preview-open'" type="button" :disabled="opening || !name" :aria-label="t('画面预览')" :title="t('画面预览')" @click="openPreview">
    <AppIcon name="monitor" :size="floating ? 20 : 15" /> <span v-if="!floating">{{ t('画面预览') }}</span>
  </button>
  <Teleport to="body">
    <div v-if="expanded" class="preview-modal-mask" @click.self="closePreview">
      <div class="preview-modal" role="dialog" aria-modal="true" :aria-label="t('画面预览')">
        <ScreenPreviewContent :name="name" :language="language" closable @close="closePreview" />
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.preview-open { white-space:nowrap; }
.preview-fab { position:fixed; z-index:60; right:24px; bottom:24px; display:inline-flex; align-items:center; justify-content:center; width:48px; height:48px; border:0; border-radius:50%; color:#fff; background:var(--accent); box-shadow:0 8px 24px rgba(0,153,255,.32); }
.preview-fab:hover { background:var(--accent-2); box-shadow:0 10px 28px rgba(0,153,255,.4); }
.preview-modal-mask { position:fixed; z-index:100; inset:0; display:flex; align-items:center; justify-content:center; padding:28px; background:rgba(0,0,0,.45); }
.preview-modal { width:min(480px,100%); height:min(860px,100%); min-height:0; filter:drop-shadow(0 16px 36px rgba(0,0,0,.24)); }
@media (max-width:760px) { .preview-fab { right:16px; bottom:16px; } }
</style>
