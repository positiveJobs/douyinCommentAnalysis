<template>
  <div class="charts">
    <div class="chart-box">
      <div ref="barRef" class="chart"></div>
    </div>
    <div class="chart-box">
      <div ref="pieRef" class="chart"></div>
    </div>
  </div>
</template>

<script setup>
import * as echarts from 'echarts'
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  viewpoints: { type: Array, default: () => [] }
})

const barRef = ref(null)
const pieRef = ref(null)
let barChart = null
let pieChart = null
let observer = null

function render() {
  const items = props.viewpoints || []
  const top = items.slice(0, 15)
  if (barChart) {
    barChart.setOption({
      title: { text: '各观点评论数量（Top 15）', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 140, right: 24, top: 44, bottom: 24 },
      xAxis: { type: 'value' },
      yAxis: {
        type: 'category',
        inverse: true,
        data: top.map((item) => item.name),
        axisLabel: { width: 130, overflow: 'truncate', fontSize: 11 }
      },
      series: [
        {
          type: 'bar',
          data: top.map((item) => item.comment_count),
          barWidth: 12,
          itemStyle: { color: '#3b82f6' },
          label: { show: true, position: 'right', fontSize: 11 }
        }
      ]
    })
  }
  if (pieChart) {
    pieChart.setOption({
      title: { text: '观点评论占比（Top 10）', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: { trigger: 'item', formatter: '{b}<br/>{c} 条（{d}%）' },
      legend: { type: 'scroll', bottom: 0, textStyle: { fontSize: 11 } },
      series: [
        {
          type: 'pie',
          radius: ['38%', '62%'],
          center: ['50%', '48%'],
          data: items.slice(0, 10).map((item) => ({ name: item.name, value: item.comment_count })),
          label: { fontSize: 11, formatter: '{b}\n{d}%' }
        }
      ]
    })
  }
}

async function resize() {
  await nextTick()
  barChart && barChart.resize()
  pieChart && pieChart.resize()
}

onMounted(async () => {
  barChart = echarts.init(barRef.value)
  pieChart = echarts.init(pieRef.value)
  render()
  await nextTick()
  resize()
  window.addEventListener('resize', resize)
  // 容器宽度变化（例如数据加载完成后表格撑开页面）时同步重绘，避免画布尺寸错位
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => resize())
    observer.observe(barRef.value)
    observer.observe(pieRef.value)
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  observer && observer.disconnect()
  barChart && barChart.dispose()
  pieChart && pieChart.dispose()
})

watch(
  () => props.viewpoints,
  async () => {
    render()
    await resize()
  },
  { deep: true }
)
</script>

<style scoped>
.charts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
  gap: 16px;
}

.chart {
  height: 380px;
  width: 100%;
}

.chart-box {
  min-width: 0;
}
</style>
