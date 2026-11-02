<script setup lang="ts">
import { computed } from 'vue'

// Renders text with **bold** emphasis and bare URLs as external links. In the Tauri
// shell target=_blank is intercepted by on_new_window and opened in the
// system browser; in a plain browser it just opens a new tab.
const props = defineProps<{ text: string }>()

// Trailing CJK/ASCII punctuation is excluded so sentences like "见 https://… 。" don't swallow the period.
const URL_RE = /https?:\/\/[^\s<>"'，。；、（）【】《》]+/g

const parts = computed(() => {
  const out: { text: string; url?: string; bold: boolean }[] = []
  for (const [index, chunk] of props.text.split(/(\*\*[\s\S]+?\*\*)/).entries()) {
    const bold = index % 2 === 1
    const text = bold ? chunk.slice(2, -2) : chunk
    let last = 0
    for (const match of text.matchAll(URL_RE)) {
      if (match.index > last) out.push({ text: text.slice(last, match.index), bold })
      out.push({ text: match[0], url: match[0], bold })
      last = match.index + match[0].length
    }
    if (last < text.length) out.push({ text: text.slice(last), bold })
  }
  return out
})
</script>

<template>
  <span><template v-for="(part, index) in parts" :key="index"><a v-if="part.url" :href="part.url" target="_blank" rel="noopener"><strong v-if="part.bold">{{ part.text }}</strong><template v-else>{{ part.text }}</template></a><strong v-else-if="part.bold">{{ part.text }}</strong><template v-else>{{ part.text }}</template></template></span>
</template>
