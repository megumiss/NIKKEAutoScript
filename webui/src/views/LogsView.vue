<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import AppIcon from '../components/AppIcon.vue'
import AppSelect from '../components/AppSelect.vue'
import { logLevelOptions, t } from '../i18n'
import { useLogsPageStore } from '../stores/logsPage'

const logsPage = useLogsPageStore()
const { logsDate, logsSource, logsLevel, logsKeyword, logsRecords, logsLoading, logsDateOptions, logsSourceOptions, logsExportUrl } = storeToRefs(logsPage)
const { logsRankClass, logsCountText, refreshLogs } = logsPage

// 查询结果更新后滚到底部（原 App.vue 中 queryLogs 内的滚动逻辑）。
const logsBody = ref<HTMLElement>()
watch(logsRecords, async () => {
  await nextTick()
  if (logsBody.value) logsBody.value.scrollTop = logsBody.value.scrollHeight
})
</script>

<template>
  <section class="view logs-view">
    <article class="card task-hero">
      <div class="task-icon"><AppIcon name="file-text" :size="22" /></div>
      <div style="flex:1"><h2>{{ t('日志') }}</h2><div class="sub">{{ t('查看 log 目录下的日志文件，支持按类型、级别、日期和关键字筛选。') }}</div></div>
      <button class="btn" @click="refreshLogs"><AppIcon name="refresh" :size="16" /> {{ t('刷新') }}</button>
      <a v-if="logsExportUrl" class="btn" :href="logsExportUrl" :download="`${logsDate}_${logsSource}.txt`"><AppIcon name="download" :size="16" /> {{ t('导出') }}</a>
    </article>
    <article class="card log-card logs-card">
      <div class="log-head logs-filter">
        <label class="logs-filter-item">{{ t('日期') }}<AppSelect v-model="logsDate" :options="logsDateOptions"/></label>
        <label class="logs-filter-item">{{ t('类型') }}<AppSelect v-model="logsSource" :options="logsSourceOptions"/></label>
        <label class="logs-filter-item">{{ t('级别') }}<AppSelect v-model="logsLevel" :options="logLevelOptions"/></label>
        <label class="logs-filter-item logs-keyword">{{ t('关键字') }}<span class="logs-kw"><input v-model="logsKeyword" :placeholder="t('搜索关键字…')"><button v-if="logsKeyword" type="button" class="logs-kw-clear" @click.prevent="logsKeyword = ''"><AppIcon name="x" :size="12" /></button></span></label>
        <span class="logs-meta">{{ logsCountText() }}<span v-if="logsLoading"> · …</span></span>
      </div>
      <div ref="logsBody" class="log-body" :class="{ 'logs-merge': !logsSource }">
        <div v-if="!logsRecords.length && !logsLoading" class="logs-empty">{{ t('没有匹配的日志') }}</div>
        <div v-for="(line, index) in logsRecords" :key="index" class="log-line" :class="[logsRankClass(line.rank), line.kind, line.section_level !== undefined ? `section-level-${line.section_level}` : '']">
          <span class="ts">{{ line.time }}</span>
          <span class="lv-chip" :class="logsRankClass(line.rank)">{{ line.level }}</span>
          <span v-if="!logsSource" class="logs-src">{{ line.source }}</span>
          <span v-if="line.kind === 'attr'" class="log-message"><span class="log-attr-key">{{ line.attr_name }}:</span><span class="log-attr-value" :class="`attr-value-${line.attr_value_kind || 'text'}`">{{ line.attr_value }}</span></span>
          <span v-else class="log-message">{{ line.text }}</span>
          <div v-if="line.traceback" class="log-traceback">
            <details v-if="line.traceback_collapsed" class="log-traceback-more">
              <summary>{{ t('详细信息') }}</summary>
              <pre class="log-traceback-collapsed">{{ line.traceback_collapsed }}</pre>
            </details>
            <pre class="log-traceback-primary">{{ line.traceback }}</pre>
          </div>
        </div>
      </div>
    </article>
  </section>
</template>
