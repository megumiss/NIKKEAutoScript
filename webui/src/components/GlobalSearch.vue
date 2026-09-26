<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute } from 'vue-router'
import AppIcon from './AppIcon.vue'
import { t } from '../i18n'
import { useSearchStore } from '../stores/search'

const route = useRoute()
const search = useSearchStore()
const { open, query, loading, settings, total, error, pageHits, hitGroups, activeIndex } = storeToRefs(search)
const { close, schedule, openPage, openHit, moveActive, activate } = search

const root = ref<HTMLElement>()
const panel = ref<HTMLElement>()
const input = ref<HTMLInputElement>()

// 每个命中分组记录自己在扁平键盘导航列表里的起始下标，行号 = startIndex + 组内下标。
const groups = computed(() => {
  let start = pageHits.value.length
  return hitGroups.value.map(group => {
    const item = { ...group, startIndex: start }
    start += group.hits.length
    return item
  })
})

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
function onInputKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') { event.preventDefault(); moveActive(1) }
  else if (event.key === 'ArrowUp') { event.preventDefault(); moveActive(-1) }
  else if (event.key === 'Enter') { event.preventDefault(); activate() }
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
// 键盘移动高亮时让目标行滚进可视区；鼠标悬停由 @mouseenter 反向同步高亮。
watch(activeIndex, index => {
  panel.value?.querySelector(`[data-gs-index="${index}"]`)?.scrollIntoView({ block: 'nearest' })
})
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
      <input ref="input" v-model="query" :placeholder="t('搜索任务、设置、页面…')" spellcheck="false" @keydown="onInputKeydown">
      <span v-if="loading" class="gs-spin"></span>
      <button v-if="query" type="button" class="gs-clear" :title="t('清空')" @click.prevent="query = ''"><AppIcon name="x" :size="12" /></button>
      <kbd class="gs-kbd" :title="t('关闭')">Esc</kbd>
    </label>

    <div v-if="open && query.trim()" ref="panel" class="gs-panel">
      <div v-if="error" class="gs-empty">{{ error }}</div>
      <template v-else>
        <template v-if="pageHits.length">
          <div class="gs-group">{{ t('页面') }}</div>
          <button v-for="(page, index) in pageHits" :key="page.key" type="button" class="gs-item" :class="{ active: activeIndex === index }"
                  :data-gs-index="index" @click="openPage(page)" @mouseenter="activeIndex = index">
            <span class="gs-ico"><AppIcon name="arrow-right" :size="14" /></span>
            <span class="gs-text"><span class="gs-title">{{ t(page.label) }}</span><span class="gs-sub">{{ t(page.group) }}</span></span>
          </button>
        </template>

        <template v-for="group in groups" :key="group.name || 'default'">
          <div class="gs-group">
            {{ group.name || t('任务与设置') }}
            <span v-if="!group.name && total > settings.length" class="gs-more">+{{ total - settings.length }}</span>
          </div>
          <div v-for="(hit, index) in group.hits" :key="`${group.name}:${hit.task}.${hit.field || ''}`" class="gs-item" tabindex="0"
               :class="{ active: activeIndex === group.startIndex + index }" :data-gs-index="group.startIndex + index"
               @click="openHit(hit, group.name || undefined)" @keydown.enter.prevent="openHit(hit, group.name || undefined)"
               @mouseenter="activeIndex = group.startIndex + index">
            <span class="gs-ico"><AppIcon :name="hit.kind === 'task' ? 'gear' : 'edit'" :size="14" /></span>
            <span class="gs-text">
              <span class="gs-title">{{ hit.title }}</span>
              <span class="gs-sub">{{ hit.task_name }}<template v-if="hit.group_name"> › {{ hit.group_name }}</template></span>
            </span>
          </div>
        </template>

        <div v-if="!pageHits.length && !settings.length && !loading" class="gs-empty">{{ t('没有匹配的结果') }}</div>
      </template>
    </div>
  </div>
</template>
