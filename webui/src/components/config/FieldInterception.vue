<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps<{ widget: 'interception_stone_import' | 'interception_stone_charts'; data?: any; busy?: boolean }>()
const emit = defineEmits<{ import: [path: string]; error: [message: string] }>()
const importPath = ref('')
const chartRoot = ref<HTMLElement>()
let charts: echarts.ECharts[] = []

function drawCharts() {
  charts.forEach(chart => chart.dispose())
  charts = []
  if (props.widget !== 'interception_stone_charts' || !chartRoot.value) return
  const series = props.data?.series || {}
  const entries = [['daily', '近 30 天', '#66b8ea'], ['weekly', '近 12 周', '#55d9a2'], ['monthly', '近 12 月', '#ffc178']]
  entries.forEach(([key, title, color], index) => {
    const element = chartRoot.value?.children[index] as HTMLElement | undefined
    if (!element) return
    const data = series[key] || { labels: [], values: [] }
    const chart = echarts.init(element)
    chart.setOption({
      animation: false, title: { text: title, textStyle: { color: '#97a0af', fontSize: 12, fontWeight: 500 } },
      grid: { left: 32, right: 8, top: 34, bottom: 26 }, xAxis: { type: 'category', data: data.labels || [], axisLabel: { color: '#646d7b', fontSize: 10 } },
      yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#262f3d' } }, axisLabel: { color: '#646d7b', fontSize: 10 } },
      tooltip: { trigger: 'axis' }, series: [{ type: 'line', smooth: true, data: data.values || [], symbol: 'circle', symbolSize: 5, lineStyle: { color }, itemStyle: { color }, areaStyle: { color, opacity: .14 } }],
    })
    charts.push(chart)
  })
}

function submit() {
  if (!importPath.value.trim()) return emit('error', '请输入截图目录。')
  emit('import', importPath.value.trim())
}

watch(() => props.data, () => nextTick(drawCharts), { deep: true, immediate: true })
onBeforeUnmount(() => charts.forEach(chart => chart.dispose()))
</script>

<template>
  <div v-if="widget === 'interception_stone_import'" class="special-field import-field">
    <input v-model="importPath" placeholder="截图目录，例如 D:\\NIKKE\\screenshots">
    <button type="button" class="btn primary sm" :disabled="busy" @click="submit">{{ busy ? '正在导入…' : '导入截图记录' }}</button>
  </div>
  <div v-else class="special-field charts-field">
    <div v-if="!data?.rows?.length" class="special-empty">暂无拦截战掉落记录。</div>
    <div ref="chartRoot" class="charts-grid"><div></div><div></div><div></div></div>
  </div>
</template>
