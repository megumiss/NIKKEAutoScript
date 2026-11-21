<script setup lang="ts">
import FloatingScreenPreview from './FloatingScreenPreview.vue'
import { usePreviewsStore } from '../stores/previews'
import { useSystemStore } from '../stores/system'

const previews = usePreviewsStore()
const system = useSystemStore()
</script>

<template>
  <Teleport to="body">
    <div class="preview-layer">
      <FloatingScreenPreview v-for="(preview, index) in previews.windows" :key="preview.name"
        :name="preview.name" :language="system.systemStatus.language" :index="index" :style="{ zIndex: preview.order }"
        @activate="previews.activate(preview.name)" @close="previews.close(preview.name)" />
    </div>
  </Teleport>
</template>

<style scoped>
.preview-layer { position:fixed; z-index:65; inset:0; pointer-events:none; }
</style>
