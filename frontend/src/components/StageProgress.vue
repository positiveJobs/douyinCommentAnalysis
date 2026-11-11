<template>
  <div class="stages">
    <div v-for="stage in stages" :key="stage.key" class="stage-row">
      <span class="stage-icon" :class="statusOf(stage.key)">
        <el-icon v-if="statusOf(stage.key) === 'done'"><Check /></el-icon>
        <el-icon v-else-if="statusOf(stage.key) === 'active'"><Loading /></el-icon>
        <span v-else class="dot">○</span>
      </span>
      <span class="stage-label" :class="{ active: statusOf(stage.key) === 'active' }">{{ stage.label }}</span>
      <el-progress
        v-if="statusOf(stage.key) === 'active'"
        class="stage-bar"
        :percentage="innerPercent(stage.key)"
        :show-text="false"
        :stroke-width="8"
      />
      <span v-else-if="statusOf(stage.key) === 'done'" class="muted stage-note">已完成</span>
    </div>
  </div>
</template>

<script setup>
import { Check, Loading } from '@element-plus/icons-vue'

const props = defineProps({
  currentStage: { type: String, default: 'pending' },
  progress: { type: Number, default: 0 },
  failedStage: { type: String, default: null }
})

// 顺序与后端 analysis/progress.py 的阶段划分保持一致
const stages = [
  { key: 'loading', label: '读取数据', from: 0, to: 1 },
  { key: 'cleaning', label: '数据清洗', from: 1, to: 3 },
  { key: 'llm_analysis', label: 'LLM 语义分析', from: 3, to: 48 },
  { key: 'embedding', label: 'Embedding 向量化', from: 48, to: 54 },
  { key: 'clustering', label: '观点语义聚类', from: 54, to: 56 },
  { key: 'viewpoint_merge', label: '观点归并命名', from: 56, to: 74 },
  { key: 'reason_analysis', label: '理由归纳', from: 74, to: 92 },
  { key: 'statistics', label: '统计人数占比', from: 92, to: 95 },
  { key: 'report', label: '生成报告', from: 95, to: 98 },
  { key: 'sampling', label: '抽样校验', from: 98, to: 100 }
]

function indexOf(stageKey) {
  return stages.findIndex((item) => item.key === stageKey)
}

function statusOf(stageKey) {
  if (props.currentStage === 'completed') return 'done'
  if (props.failedStage === stageKey) return 'failed'
  const current = indexOf(props.currentStage)
  const self = indexOf(stageKey)
  if (current === -1 || (props.currentStage === 'pending' && props.progress === 0)) return 'pending'
  if (self < current) return 'done'
  if (self === current) return 'active'
  return 'pending'
}

function innerPercent(stageKey) {
  const stage = stages.find((item) => item.key === stageKey)
  if (!stage) return 0
  const span = Math.max(stage.to - stage.from, 0.01)
  return Math.min(100, Math.max(0, ((props.progress - stage.from) / span) * 100))
}
</script>

<style scoped>
.stage-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 0;
}

.stage-icon {
  width: 20px;
  text-align: center;
  color: #9ca3af;
}

.stage-icon.done { color: #16a34a; }
.stage-icon.active { color: #2563eb; }
.stage-icon.failed { color: #dc2626; }

.stage-label {
  width: 130px;
  color: #4b5563;
  font-size: 14px;
}

.stage-label.active { color: #2563eb; font-weight: 600; }

.stage-bar { flex: 1; }
.stage-note { width: 60px; }
.dot { font-size: 12px; }
</style>
