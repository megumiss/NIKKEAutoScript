<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps<{ widget: 'interception_stone_import' | 'interception_stone_charts'; data?: any; busy?: boolean }>()
const emit = defineEmits<{ import: [path: string]; error: [message: string] }>()
const importPath = ref('')
const chartRoot = ref<HTMLElement>()
let charts: echarts.ECharts[] = []

function cssVar(name: string, fallback: string) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback }

function drawCharts() {
  charts.forEach(chart => chart.dispose())
  charts = []
  if (props.widget !== 'interception_stone_charts' || !chartRoot.value) return
  const series = props.data?.series || {}
  const text3 = cssVar('--text-3', '#97a0af')
  const border = cssVar('--border', '#262f3d')
  const entries = [['daily', '近 30 天', '#66b8ea'], ['weekly', '近 12 周', '#55d9a2'], ['monthly', '近 12 月', '#ffc178']]
  entries.forEach(([key, title, color], index) => {
    const element = chartRoot.value?.children[index] as HTMLElement | undefined
    if (!element) return
    const data = series[key] || { labels: [], values: [] }
    const chart = echarts.init(element)
    chart.setOption({
      animationDuration: 600, title: { text: title, textStyle: { color: text3, fontSize: 12, fontWeight: 500 } },
      grid: { left: 36, right: 14, top: 34, bottom: 26 },
      xAxis: { type: 'category', boundaryGap: false, data: data.labels || [], axisLabel: { color: text3, fontSize: 10 }, axisLine: { lineStyle: { color: border } } },
      yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: border } }, axisLabel: { color: text3, fontSize: 10 } },
      tooltip: { trigger: 'axis', axisPointer: { type: 'line', lineStyle: { color: text3 } } },
      series: [{
        type: 'line', smooth: true, data: data.values || [], symbol: 'circle', symbolSize: 5, showSymbol: false,
        lineStyle: { color, width: 2 }, itemStyle: { color }, emphasis: { focus: 'series', scale: 1.6 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: `${color}55` }, { offset: 1, color: `${color}00` },
          ]),
        },
      }],
    })
    charts.push(chart)
  })
}

function onResize() { charts.forEach(chart => chart.resize()) }

function submit() {
  if (!importPath.value.trim()) return emit('error', '请输入截图目录。')
  emit('import', importPath.value.trim())
}

watch(() => props.data, () => nextTick(drawCharts), { deep: true, immediate: true })
onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); charts.forEach(chart => chart.dispose()) })
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
