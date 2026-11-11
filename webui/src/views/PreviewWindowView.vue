<script setup lang="ts">
import { onMounted } from 'vue'
import ScreenPreviewContent from '../components/ScreenPreviewContent.vue'
import { t, uiLanguage } from '../i18n'

const props = defineProps<{ name: string; language: string }>()
uiLanguage.value = props.language

onMounted(() => {
  document.title = `${t('画面预览')} - ${props.name}`
})
</script>

<template>
  <main class="preview-window">
    <ScreenPreviewContent :name="name" :language="language" />
  </main>
</template>

<style>
body.preview-window-body { min-width: 0; }
.preview-window { width: 100%; height: 100vh; padding: 12px; }
</style>
