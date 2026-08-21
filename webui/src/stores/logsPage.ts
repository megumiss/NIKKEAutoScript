import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { api } from '../api/client'
import { t } from '../i18n'
import router from '../router'
import type { LogFileRef } from '../types'
import { useToastStore } from './toast'
import { useSystemStore } from './system'
import { useInstancesStore } from './instances'

// Logs page: filtered viewer over the ./log/<date>_<source>.txt files.  The
// backend folds continuation lines (tracebacks) into the record opened by
// the last levelled line and returns the newest matching records; the level
// select reuses the live log's debug/info/warn/err threshold options.
export const useLogsPageStore = defineStore('logsPage', () => {
  const toast = useToastStore()
  const logFiles = ref<LogFileRef[]>([])
  const logsDate = ref('')
  const logsSource = ref('')
  const logsLevel = ref('info')
  const logsKeyword = ref('')
  const logsRecords = ref<any[]>([])
  const logsMatched = ref(0)
  const logsTruncated = ref(false)
  const logsLoading = ref(false)
  const logsDateOptions = computed(() => [...new Set(logFiles.value.map(file => file.date))].map(date => ({ value: date, label: date })))
  const logsSourceOptions = computed(() => [{ value: '', label: t('全部') }, ...[...new Set(logFiles.value.filter(file => file.date === logsDate.value).map(file => file.source))].map(source => ({ value: source, label: source }))])
  const LOGS_RANK_CLASS = ['lv-debug', 'lv-info', 'lv-warn', 'lv-err']
  function logsRankClass(rank: number) { return LOGS_RANK_CLASS[rank] || 'lv-info' }
  function logsCountText() {
    const shown = logsRecords.value.length
    const language = useSystemStore().systemStatus.language
    if (language === 'en-US') return logsTruncated.value ? `Matched ${logsMatched.value}, showing the latest ${shown}` : `${logsMatched.value} entries`
    if (language === 'ja-JP') return logsTruncated.value ? `${logsMatched.value} 件一致、最新 ${shown} 件を表示` : `${logsMatched.value} 件`
    return logsTruncated.value ? `共匹配 ${logsMatched.value} 条，仅显示最近 ${shown} 条` : `共 ${logsMatched.value} 条`
  }
  // Default the type filter to the first instance that logged on the selected
  // date rather than the merged 全部 view; only re-pick when the current source
  // has no file for the date.  全部 stays selectable once initialized.
  let logsSourceInitialized = false
  async function loadLogFiles() {
    try {
      logFiles.value = (await api.get('/api/system/logs/files')).files || []
      if (!logsDate.value || !logFiles.value.some(file => file.date === logsDate.value)) logsDate.value = logFiles.value[0]?.date || ''
      const sources = new Set(logFiles.value.filter(file => file.date === logsDate.value).map(file => file.source))
      if (!logsSourceInitialized || (logsSource.value !== '' && !sources.has(logsSource.value))) {
        logsSource.value = useInstancesStore().instances.find(item => sources.has(item.name))?.name || [...sources][0] || ''
        logsSourceInitialized = true
      }
    } catch (exception: any) { toast.error = exception.message }
  }
  // A stale response must not overwrite a newer query: rapid keyword typing or
  // the loadLogFiles date watch can fire several queries at once.
  let logsQuerySeq = 0
  async function queryLogs() {
    if (router.currentRoute.value.path !== '/logs') return
    const seq = ++logsQuerySeq
    if (!logsDate.value) { logsRecords.value = []; logsMatched.value = 0; logsTruncated.value = false; return }
    logsLoading.value = true
    try {
      const params = new URLSearchParams({ date: logsDate.value, source: logsSource.value, level: logsLevel.value, keyword: logsKeyword.value })
      const result = await api.get(`/api/system/logs?${params}`)
      if (seq !== logsQuerySeq) return
      logsRecords.value = result.records || []
      logsMatched.value = result.matched || 0
      logsTruncated.value = Boolean(result.truncated)
    } catch (exception: any) { toast.error = exception.message } finally { if (seq === logsQuerySeq) logsLoading.value = false }
  }
  async function refreshLogs() { await loadLogFiles(); await queryLogs() }
  // Raw download of the selected file with no level/keyword filtering.  The
  // merged 全部 view has no single backing file, so the export button is
  // hidden while it is selected.
  const logsExportUrl = computed(() => logsDate.value && logsSource.value
    ? `/api/system/logs/download?date=${encodeURIComponent(logsDate.value)}&source=${encodeURIComponent(logsSource.value)}`
    : '')
  let logsKeywordTimer = 0
  watch([logsDate, logsSource, logsLevel], queryLogs)
  watch(logsKeyword, () => { window.clearTimeout(logsKeywordTimer); logsKeywordTimer = window.setTimeout(queryLogs, 400) })
  return {
    logFiles, logsDate, logsSource, logsLevel, logsKeyword, logsRecords, logsMatched, logsTruncated, logsLoading,
    logsDateOptions, logsSourceOptions, logsRankClass, logsCountText, loadLogFiles, queryLogs, refreshLogs, logsExportUrl,
  }
})
