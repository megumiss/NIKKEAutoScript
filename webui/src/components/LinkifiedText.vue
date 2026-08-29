<script setup lang="ts">
import { computed } from 'vue'

// Renders plain text with bare URLs turned into external links. In the Tauri
// shell target=_blank is intercepted by on_new_window and opened in the
// system browser; in a plain browser it just opens a new tab.
const props = defineProps<{ text: string }>()

// Trailing CJK/ASCII punctuation is excluded so sentences like "见 https://… 。" don't swallow the period.
const URL_RE = /https?:\/\/[^\s<>"'，。；、（）【】《》]+/g

const parts = computed(() => {
  const out: { text: string; url?: string }[] = []
  let last = 0
  for (const match of props.text.matchAll(URL_RE)) {
    if (match.index > last) out.push({ text: props.text.slice(last, match.index) })
    out.push({ text: match[0], url: match[0] })
    last = match.index + match[0].length
  }
  if (last < props.text.length) out.push({ text: props.text.slice(last) })
  return out
})
</script>

<template>
  <span><template v-for="(part, index) in parts" :key="index"><a v-if="part.url" :href="part.url" target="_blank" rel="noopener">{{ part.text }}</a><template v-else>{{ part.text }}</template></template></span>
</template>
