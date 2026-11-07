<script setup lang="ts">
import { nextTick, ref } from 'vue'
import AppIcon from './AppIcon.vue'
import { usePreviewsStore } from '../stores/previews'
import { t } from '../i18n'

const props = withDefaults(defineProps<{ name: string; language: string; floating?: boolean }>(), { floating: false })
const previews = usePreviewsStore()
const opening = ref(false)
const isTauri = Boolean((window as any).__TAURI_INTERNALS__)

async function openPreview() {
  if (opening.value) return
  if (!isTauri) {
    openFloatingPreview()
    return
  }
  opening.value = true
  try {
    await (window as any).__TAURI__.core.invoke('open_preview_window', { name: props.name, language: props.language })
  } catch {
    openFloatingPreview()
  } finally {
    opening.value = false
  }
}

async function openFloatingPreview() {
  previews.open(props.name)
  await nextTick()
  const preview = [...document.querySelectorAll<HTMLElement>('.floating-preview')].find(item => item.dataset.previewName === props.name)
  preview?.querySelector<HTMLElement>('.floating-preview-header')?.focus({ preventScroll: true })
}
</script>

<template>
  <button :class="floating ? 'preview-open preview-fab' : 'btn preview-open'" type="button" :disabled="opening || !name" :aria-label="t('画面预览')" :title="t('画面预览')" @click="openPreview">
    <AppIcon name="monitor" :size="floating ? 20 : 15" /> <span v-if="!floating">{{ t('画面预览') }}</span>
  </button>
</template>

<style scoped>
.preview-open { white-space:nowrap; }
.preview-fab { position:fixed; z-index:60; right:24px; bottom:24px; display:inline-flex; align-items:center; justify-content:center; width:48px; height:48px; border:0; border-radius:50%; color:#fff; background:var(--accent); box-shadow:0 8px 24px rgba(0,153,255,.32); }
.preview-fab:hover { background:var(--accent-2); box-shadow:0 10px 28px rgba(0,153,255,.4); }
@media (max-width:760px) { .preview-fab { right:16px; bottom:16px; } }
</style>
