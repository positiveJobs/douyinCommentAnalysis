<template>
  <div class="page">
    <div class="card" v-loading="loading">
      <div class="head">
        <div>
          <p class="section-title">分析结果</p>
          <p class="muted">任务ID：{{ taskId }}</p>
          <p class="muted" v-if="meta.post_topic">讨论主题：{{ meta.post_topic }}</p>
          <p class="muted" v-else>讨论主题：未提供（由评论内容判断）</p>
        </div>
        <div class="head-actions">
          <el-button @click="goReport">查看完整报告</el-button>
          <el-button @click="download('html')">下载 HTML 报告</el-button>
          <el-button @click="download('markdown')">下载 Markdown</el-button>
          <el-button @click="download('json')">下载 JSON</el-button>
          <el-button type="primary" @click="download('package')">打包下载</el-button>
        </div>
      </div>
    </div>

    <SummaryCards v-if="summary" :summary="summary" />

    <div class="card">
      <ViewpointCharts :viewpoints="viewpoints" />
      <p class="muted">
        提示：一条评论可能包含多个观点，因此各观点评论数之和可能超过有效评论数，占比之和也可能超过 100%。
      </p>
    </div>

    <div class="card">
      <p class="section-title">观点列表（按评论数量排序）</p>
      <p class="muted">排序仅表示评论数量从高到低，不代表对观点价值的评价。</p>
      <el-table :data="viewpoints" size="default" v-loading="loading" empty-text="暂无观点">
        <el-table-column prop="viewpoint_id" label="编号" width="90" />
        <el-table-column prop="name" label="观点" min-width="240" />
        <el-table-column prop="comment_count" label="评论数量" width="110" sortable />
        <el-table-column prop="percentage" label="占比" width="100">
          <template #default="{ row }">{{ row.percentage }}%</template>
        </el-table-column>
        <el-table-column label="立场" width="90">
          <template #default="{ row }">{{ stanceText(row.stance) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row.viewpoint_id)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <ViewpointDetailDrawer
      v-model:visible="drawerVisible"
      :task-id="taskId"
      :viewpoint-id="activeViewpointId"
    />
  </div>
</template>

<script setup>
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import SummaryCards from '../components/SummaryCards.vue'
import ViewpointCharts from '../components/ViewpointCharts.vue'
import ViewpointDetailDrawer from '../components/ViewpointDetailDrawer.vue'
import { downloadUrl, getSummary, getViewpoints } from '../api'

const route = useRoute()
const router = useRouter()
const taskId = route.params.taskId
const summary = ref(null)
const meta = ref({})
const viewpoints = ref([])
const loading = ref(false)
const drawerVisible = ref(false)
const activeViewpointId = ref(null)

const STANCES = { support: '支持', oppose: '反对', conditional: '有条件', neutral: '中立', unclear: '不明' }
function stanceText(stance) {
  return STANCES[stance] || stance || '不明'
}

function openDetail(viewpointId) {
  activeViewpointId.value = viewpointId
  drawerVisible.value = true
}

function download(kind) {
  window.open(downloadUrl(taskId, kind), '_blank')
}

function goReport() {
  router.push({ name: 'report', params: { taskId } })
}

async function load() {
  loading.value = true
  try {
    const summaryPayload = await getSummary(taskId)
    summary.value = summaryPayload.summary
    meta.value = summaryPayload.meta || {}
    const viewpointPayload = await getViewpoints(taskId)
    viewpoints.value = viewpointPayload.items
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}

.head-actions {
  display: flex;
  gap: 8px;
}
</style>
