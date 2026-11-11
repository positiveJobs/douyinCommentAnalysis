<template>
  <div class="page">
    <div class="card">
      <div class="head">
        <div>
          <p class="section-title">正在分析</p>
          <p class="muted">任务ID：{{ taskId }}</p>
          <p class="muted" v-if="task && task.filename">文件：{{ task.filename }}</p>
        </div>
        <el-tag v-if="task" :type="statusType(task.status)">{{ statusText(task.status) }}</el-tag>
      </div>

      <el-alert
        v-if="task && task.status === 'failed'"
        type="error"
        :closable="false"
        show-icon
        title="分析失败"
        class="alert"
      >
        <div>失败阶段：{{ task.stage_label || task.error_stage || '未知' }}</div>
        <div>原因：{{ task.error }}</div>
        <div>任务ID：{{ task.task_id }}</div>
      </el-alert>

      <StageProgress
        v-if="task && task.status !== 'failed'"
        :current-stage="task.stage"
        :progress="task.progress || 0"
      />

      <div v-if="task" class="status-block">
        <div class="status-message">{{ task.message || '—' }}</div>
        <div class="status-grid">
          <div>
            <span class="muted">当前阶段：</span>{{ task.stage_label || '—' }}
          </div>
          <div v-if="task.total_steps">
            <span class="muted">阶段进度：</span>{{ task.current_step || 0 }} / {{ task.total_steps }}
          </div>
          <div><span class="muted">总进度：</span>{{ Math.round(task.progress || 0) }}%</div>
          <div><span class="muted">已运行：</span>{{ elapsedText }}</div>
        </div>
      </div>

      <div v-if="task && task.status === 'completed'" class="done-block">
        <el-result icon="success" title="分析完成" :sub-title="doneSubTitle">
          <template #extra>
            <el-button type="primary" @click="goResult">查看分析结果</el-button>
            <el-button @click="download('html')">下载 HTML 报告</el-button>
            <el-button @click="download('package')">打包下载</el-button>
          </template>
        </el-result>
      </div>

      <div class="actions">
        <el-button @click="refresh" :loading="loading">刷新状态</el-button>
        <el-button
          v-if="task && (task.status === 'running' || task.status === 'pending')"
          type="danger"
          plain
          :loading="cancelling"
          @click="cancel"
        >
          取消分析
        </el-button>
        <el-button v-if="task && task.status === 'failed'" type="primary" @click="goHome">重新上传</el-button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import StageProgress from '../components/StageProgress.vue'
import { cancelTask, downloadUrl, getTask } from '../api'

const route = useRoute()
const router = useRouter()
const taskId = route.params.taskId
const task = ref(null)
const loading = ref(false)
const cancelling = ref(false)
const now = ref(Date.now())
let timer = null
let ticker = null

const doneSubTitle = computed(() => {
  if (!task.value) return ''
  return `共分析 ${task.value.comment_count ?? 0} 条评论，识别出 ${task.value.viewpoint_count ?? 0} 个核心观点`
})

// 已运行时间：优先用任务真实开始时间，其次是页面开始观察的时间
const startedAt = computed(() => {
  const raw = task.value?.started_at || task.value?.created_at
  if (!raw) return null
  const parsed = new Date(raw.replace('T', ' ').replace(' ', 'T'))
  return Number.isNaN(parsed.getTime()) ? null : parsed.getTime()
})

const elapsedText = computed(() => {
  const start = startedAt.value
  if (!start) return '—'
  const end = task.value?.finished_at
    ? new Date(String(task.value.finished_at).replace('T', ' ').replace(' ', 'T')).getTime()
    : now.value
  const seconds = Math.max(0, Math.round((end - start) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  if (minutes < 60) return `${minutes} 分 ${String(rest).padStart(2, '0')} 秒`
  const hours = Math.floor(minutes / 60)
  return `${hours} 小时 ${String(minutes % 60).padStart(2, '0')} 分`
})

function statusText(status) {
  return { pending: '排队中', running: '分析中', completed: '已完成', failed: '失败' }[status] || status
}

function statusType(status) {
  return { pending: 'info', running: 'warning', completed: 'success', failed: 'danger' }[status] || 'info'
}

async function refresh() {
  loading.value = true
  try {
    const payload = await getTask(taskId)
    task.value = payload
    if (payload.status === 'completed' || payload.status === 'failed') {
      stopPolling()
      if (payload.status === 'failed') ElMessage.error(payload.error || '分析失败')
    }
  } catch (err) {
    ElMessage.error(err.message)
    stopPolling()
  } finally {
    loading.value = false
  }
}

function startPolling() {
  stopPolling()
  // 每 1.5 秒轮询一次真实状态，完成或失败即停止
  timer = setInterval(refresh, 1500)
  // 每秒刷新“已运行时间”
  ticker = setInterval(() => {
    now.value = Date.now()
  }, 1000)
}

function stopPolling() {
  if (timer) clearInterval(timer)
  timer = null
  if (ticker) clearInterval(ticker)
  ticker = null
}

function goResult() {
  router.push({ name: 'result', params: { taskId } })
}

function goHome() {
  router.push({ name: 'home' })
}

function download(kind) {
  window.open(downloadUrl(taskId, kind), '_blank')
}

async function cancel() {
  try {
    await ElMessageBox.confirm(
      '取消后本次任务分析会立即停止，并且会删除该任务产生的全部数据（上传文件、中间结果、结果与报告），且无法恢复。确定取消吗？',
      '取消分析',
      { type: 'warning', confirmButtonText: '取消分析', cancelButtonText: '继续分析' }
    )
  } catch {
    return
  }
  cancelling.value = true
  try {
    await cancelTask(taskId)
    ElMessage.info('已请求取消：会等当前批次返回后停止，并清理本次任务的数据…')
    // 等待数据清理完成（任务记录会被删除，接口返回 404 即表示清理完成）
    const deadline = Date.now() + 120000
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 1500))
      try {
        const payload = await getTask(taskId)
        if (payload.status === 'cancelled') break
      } catch (err) {
        // 404：任务记录已删除，说明数据已清理
        ElMessage.success('任务已取消，相关数据已删除')
        router.push({ name: 'tasks' })
        return
      }
    }
    ElMessage.warning('取消请求已发送，请稍后在任务记录页确认')
    router.push({ name: 'tasks' })
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    cancelling.value = false
  }
}

onMounted(async () => {
  await refresh()
  if (task.value && (task.value.status === 'pending' || task.value.status === 'running')) startPolling()
})

onBeforeUnmount(stopPolling)
</script>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.status-block {
  margin-top: 16px;
  line-height: 1.9;
  font-size: 14px;
}

.status-message {
  background: #f8fafc;
  border-left: 3px solid #93c5fd;
  border-radius: 6px;
  padding: 10px 12px;
  font-size: 14px;
  color: #1f2329;
}

.status-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 28px;
  margin-top: 10px;
}

.alert {
  margin: 12px 0;
}

.actions {
  margin-top: 12px;
}
</style>
