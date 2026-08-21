<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import AppSelect from './AppSelect.vue'
import { logLevelOptions, t } from '../i18n'
import { useWorkspaceStore } from '../stores/workspace'

const workspace = useWorkspaceStore()
const { visibleLogs, logLevel, autoScroll, logTick } = storeToRefs(workspace)

// 自动滚动：日志批次到达（logTick 自增）后滚到底部；缓冲饱和后长度不变，
// 所以监听 tick 而不是列表长度。
const logBody = ref<HTMLElement>()
watch(logTick, async () => {
  if (!autoScroll.value) return
  await nextTick()
  if (logBody.value) logBody.value.scrollTop = logBody.value.scrollHeight
})
</script>

<template>
  <article class="card log-card">
    <div class="log-head"><b>{{ t('实时日志') }}</b><span class="log-autoscroll"><AppSelect class="log-level-select" v-model="logLevel" :options="logLevelOptions"/><label class="switch sm"><input v-model="autoScroll" type="checkbox"><span class="slider"></span></label>{{ t('自动滚动') }}</span></div>
    <div ref="logBody" class="log-body">
      <div v-for="line in visibleLogs" :key="line.id" class="log-frag" v-html="line.html"></div>
    </div>
  </article>
</template>
