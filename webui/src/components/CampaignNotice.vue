<script setup lang="ts">
import { ref, watch } from 'vue'
import AppIcon from './AppIcon.vue'
import { api } from '../api/client'
import { t, uiLanguage } from '../i18n'

type Notice = { title: string; summary: string; details_html: string }
const notice = ref<Notice | null>(null)

watch(uiLanguage, async (language, _, onCleanup) => {
  let discarded = false
  onCleanup(() => { discarded = true })
  notice.value = null
  try {
    const result = await api.get(`/api/campaign/notice?language=${encodeURIComponent(language)}`)
    if (!discarded) notice.value = result.notice || null
  } catch {
    if (!discarded) notice.value = null
  }
}, { immediate: true })
</script>

<template>
  <details v-if="notice" :key="uiLanguage" class="campaign-notice">
    <summary class="campaign-notice-summary">
      <AppIcon name="alert-triangle" :size="17" color="currentColor" class="campaign-notice-icon" />
      <strong v-if="notice.title" class="campaign-notice-title">{{ notice.title }}</strong>
      <span v-if="notice.summary" class="campaign-notice-message">{{ notice.summary }}</span>
      <span class="campaign-notice-toggle">
        <span class="when-closed">{{ t('展开详情') }}</span>
        <span class="when-open">{{ t('收起详情') }}</span>
        <AppIcon name="arrow-right" :size="14" color="currentColor" />
      </span>
    </summary>
    <!-- 正文由后端 Markdown 渲染器禁用 HTML／图片后生成，不将原始文件直接作为 HTML 插入。 -->
    <div v-if="notice.details_html" class="campaign-notice-details" v-html="notice.details_html"></div>
  </details>
</template>

<style scoped>
.campaign-notice {
  flex: none;
  margin-bottom: 18px;
  border: 1px solid var(--border);
  border-left: 3px solid var(--yellow);
  border-radius: 10px;
  background: var(--yellow-soft);
}
.campaign-notice-summary {
  display: flex;
  gap: 9px;
  align-items: center;
  min-height: 46px;
  padding: 10px 16px;
  list-style: none;
  cursor: pointer;
  font-size: 13px;
  line-height: 1.6;
}
.campaign-notice-summary::-webkit-details-marker { display: none; }
.campaign-notice-summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; border-radius: 8px; }
.campaign-notice-icon { flex: none; color: var(--yellow); }
.campaign-notice-title { flex: none; color: var(--text); }
.campaign-notice-message { flex: 1; min-width: 0; color: var(--text); }
.campaign-notice-toggle { margin-left: auto; display: flex; flex: none; gap: 5px; align-items: center; color: var(--text-2); font-size: 12px; }
.campaign-notice-summary:hover .campaign-notice-toggle { color: var(--text); }
.when-open { display: none; }
.campaign-notice[open] .when-open { display: inline; }
.campaign-notice[open] .when-closed { display: none; }
.campaign-notice-toggle .app-icon { transform: rotate(90deg); }
.campaign-notice[open] .campaign-notice-toggle .app-icon { transform: rotate(270deg); }
.campaign-notice-details { margin: 0 16px 12px 48px; color: var(--text-2); font-size: 12.5px; line-height: 1.8; overflow-wrap: anywhere; }
.campaign-notice-details :deep(ul), .campaign-notice-details :deep(ol) { padding-left: 17px; }
.campaign-notice-details :deep(li + li) { margin-top: 3px; }
.campaign-notice-details :deep(p + p), .campaign-notice-details :deep(p + ul),
.campaign-notice-details :deep(p + ol), .campaign-notice-details :deep(ul + p) { margin-top: 6px; }
.campaign-notice-details :deep(a) { color: var(--accent); }
.campaign-notice-details :deep(h2), .campaign-notice-details :deep(h3) { margin: 8px 0 4px; font-size: 13px; color: var(--text); }
.campaign-notice-details :deep(pre) { padding: 8px; background: var(--card-2); overflow: auto; }
.campaign-notice-details :deep(blockquote) { border-left: 2px solid var(--border-light); padding-left: 10px; }
@media (max-width: 640px) {
  .campaign-notice-summary { flex-wrap: wrap; gap: 6px 8px; padding: 10px 12px; }
  .campaign-notice-message { flex-basis: 100%; order: 1; }
  .campaign-notice-toggle { margin-left: auto; }
  .campaign-notice-details { margin-left: 24px; }
}
</style>
