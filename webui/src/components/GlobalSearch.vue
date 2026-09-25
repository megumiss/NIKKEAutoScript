<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'
import AppIcon from './AppIcon.vue'
import { t } from '../i18n'
import { useSearchStore } from '../stores/search'

const route = useRoute()
const search = useSearchStore()
const { open, query, loading, settings, total, error, pageHits, targetInstance, multiInstance, instanceNames } = storeToRefs(search)
const { close, schedule, openPage, openHit } = search

const root = ref<HTMLElement>()
const input = ref<HTMLInputElement>()

function focusInput() { nextTick(() => input.value?.focus()) }
function toggle() {
  search.toggle()
  if (open.value) focusInput()
}
function onKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    toggle()
    return
  }
  if (event.key === 'Escape' && open.value) close()
}
// 用 mousedown 而不是 blur 判断点外：在输入框之间切换焦点不会误关面板。
function onPointerDown(event: MouseEvent) {
  if (open.value && root.value && !root.value.contains(event.target as Node)) close()
}
onMounted(() => {
  window.addEventListener('keydown', onKeydown)
  document.addEventListener('mousedown', onPointerDown)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown)
  document.removeEventListener('mousedown', onPointerDown)
})
watch(query, schedule)
watch(open, value => { if (value) focusInput() })
watch(() => route.fullPath, () => { if (open.value) close() })
</script>

<template>
  <div ref="root" class="global-search">
    <button v-if="!open" type="button" class="gs-trigger" :title="t('搜索整个脚本')" @click="toggle">
      <AppIcon name="search-normal" :size="15" />
      <span class="gs-trigger-label">{{ t('搜索整个脚本') }}</span>
      <kbd class="gs-kbd">Ctrl K</kbd>
    </button>
    <label v-else class="gs-box">
      <AppIcon name="search-normal" :size="14" />
      <input ref="input" v-model="query" :placeholder="t('搜索任务、设置、页面…')" spellcheck="false">
      <span v-if="loading" class="gs-spin"></span>
      <button v-if="query" type="button" class="gs-clear" :title="t('清空')" @click.prevent="query = ''"><AppIcon name="x" :size="12" /></button>
      <button type="button" class="gs-clear" :title="t('关闭')" @click.prevent="close"><AppIcon name="x" :size="12" /></button>
    </label>

    <div v-if="open && query.trim()" class="gs-panel">
      <div v-if="error" class="gs-empty">{{ error }}</div>
      <template v-else>
        <template v-if="pageHits.length">
          <div class="gs-group">{{ t('页面') }}</div>
          <button v-for="page in pageHits" :key="page.key" type="button" class="gs-item" @click="openPage(page)">
            <span class="gs-ico"><AppIcon name="arrow-right" :size="14" /></span>
            <span class="gs-text"><span class="gs-title">{{ t(page.label) }}</span><span class="gs-sub">{{ t(page.group) }}</span></span>
          </button>
        </template>

        <template v-if="settings.length">
          <div class="gs-group">
            {{ t('任务与设置') }}
            <span v-if="total > settings.length" class="gs-more">+{{ total - settings.length }}</span>
          </div>
          <div v-for="hit in settings" :key="`${hit.task}.${hit.field || ''}`" class="gs-item" tabindex="0" @click="openHit(hit)" @keydown.enter.prevent="openHit(hit)">
            <span class="gs-ico"><AppIcon :name="hit.kind === 'task' ? 'gear' : 'edit'" :size="14" /></span>
            <span class="gs-text">
              <span class="gs-title">{{ hit.title }}</span>
              <span class="gs-sub">{{ hit.task_name }}<template v-if="hit.group_name"> › {{ hit.group_name }}</template></span>
            </span>
            <span v-if="multiInstance" class="gs-inst">
              <button v-for="name in instanceNames" :key="name" type="button" class="gs-inst-chip" :class="{ active: name === targetInstance }" :title="name" @click.stop="openHit(hit, name)">{{ name }}</button>
            </span>
          </div>
        </template>

        <div v-if="!pageHits.length && !settings.length && !loading" class="gs-empty">{{ t('没有匹配的结果') }}</div>
      </template>
    </div>
  </div>
</template>
