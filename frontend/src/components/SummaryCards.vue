<template>
  <el-row :gutter="12">
    <el-col v-for="card in cards" :key="card.label" :xs="12" :sm="8" :md="4">
      <div class="card stat-card">
        <div class="muted">{{ card.label }}</div>
        <div class="stat-value">{{ card.value }}</div>
        <div v-if="card.hint" class="muted">{{ card.hint }}</div>
      </div>
    </el-col>
  </el-row>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  summary: { type: Object, required: true }
})

const cards = computed(() => {
  const s = props.summary || {}
  return [
    { label: '评论总数', value: s.raw_comment_count ?? 0, hint: '原始文件条数' },
    { label: '有效评论', value: s.valid_comment_count ?? 0, hint: '进入语义分析' },
    { label: '无关评论', value: s.irrelevant_comment_count ?? 0, hint: '与主题无关' },
    { label: '无法判断', value: s.unclear_comment_count ?? 0, hint: '没有明确观点' },
    { label: '最终观点数', value: s.viewpoint_count ?? 0, hint: '归并后核心观点' },
    {
      label: '观点-评论关联',
      value: s.viewpoint_comment_link_count ?? 0,
      hint: '一条评论可含多个观点'
    }
  ]
})
</script>

<style scoped>
.stat-card {
  margin-bottom: 12px;
  padding: 14px 16px;
}
</style>
