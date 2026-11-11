<template>
  <div class="page">
    <div class="card">
      <div class="head">
        <div>
          <p class="section-title">任务记录</p>
          <p class="muted">这里保存了所有分析过的任务，可随时回来查看结果或重新下载报告。</p>
        </div>
        <el-button type="primary" @click="$router.push({ name: 'home' })">上传新文件</el-button>
      </div>

      <div class="filters">
        <el-radio-group v-model="statusFilter" @change="reload">
          <el-radio-button value="">全部 ({{ statusCounts.all || 0 }})</el-radio-button>
          <el-radio-button value="running">分析中 ({{ (statusCounts.running || 0) + (statusCounts.pending || 0) }})</el-radio-button>
          <el-radio-button value="completed">已完成 ({{ statusCounts.completed || 0 }})</el-radio-button>
          <el-radio-button value="failed">失败 ({{ statusCounts.failed || 0 }})</el-radio-button>
        </el-radio-group>
        <div class="filter-right">
          <el-input
            v-model="keyword"
            placeholder="按文件名搜索"
            clearable
            style="width: 200px"
            @keyup.enter="reload"
            @clear="reload"
          />
          <el-button @click="reload" :loading="loading">查询</el-button>
          <el-switch v-model="autoRefresh" active-text="自动刷新" />
        </div>
      </div>
    </div>

    <div class="card">
      <el-table :data="items" v-loading="loading" empty-text="暂无任务" @row-click="onRowClick">
        <el-table-column type="expand" width="46">
          <template #default="{ row }">
            <div class="expand">
              <div><span class="muted">任务 ID：</span>{{ row.task_id }}</div>
              <div v-if="row.topic"><span class="muted">主题：</span>{{ row.topic }}</div>
              <div><span class="muted">当前阶段：</span>{{ row.stage_label || '—' }}</div>
              <div><span class="muted">耗时：</span>{{ row.elapsed_seconds ? row.elapsed_seconds + ' 秒' : '—' }}</div>
              <div v-if="row.message"><span class="muted">状态说明：</span>{{ row.message }}</div>
              <div v-if="row.error" class="error"><span class="muted">失败原因：</span>{{ row.error }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="filename" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="140">
          <template #default="{ row }">
            <el-progress
              :percentage="Math.round(row.progress || 0)"
              :stroke-width="8"
              :status="row.status === 'failed' ? 'exception' : row.status === 'completed' ? 'success' : ''"
            />
          </template>
        </el-table-column>
        <el-table-column prop="comment_count" label="评论数" width="90" />
        <el-table-column prop="viewpoint_count" label="观点数" width="90" />
        <el-table-column label="创建时间" width="150">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="300">
          <template #default="{ row }">
            <template v-if="row.status === 'completed'">
              <el-button link type="primary" @click.stop="go('result', row)">查看结果</el-button>
              <el-button link type="primary" @click.stop="go('report', row)">查看报告</el-button>
              <el-dropdown trigger="click" @command="(cmd) => download(row, cmd)">
                <template #dropdown>
                  <el-dropdown-menu>
                    <el-dropdown-item command="html">HTML 报告（含数据）</el-dropdown-item>
                    <el-dropdown-item command="markdown">Markdown 报告</el-dropdown-item>
                    <el-dropdown-item command="json">结果 JSON</el-dropdown-item>
                    <el-dropdown-item command="package" divided>打包下载（zip）</el-dropdown-item>
                  </el-dropdown-menu>
                </template>
              </el-dropdown>
              <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
            </template>
            <template v-else-if="row.status === 'failed'">
              <el-button link type="danger" @click.stop="go('task', row)">查看原因</el-button>
              <el-button link type="danger" @click.stop="remove(row)">删除</el-button>
            </template>
            <template v-else>
              <el-button link type="primary" @click.stop="go('task', row)">查看进度</el-button>
              <el-button link type="danger" @click.stop="cancel(row)">取消分析</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        class="pager"
        layout="total, sizes, prev, pager, next"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        :page-sizes="[10, 20, 50]"
        @current-change="onPageChange"
        @size-change="onSizeChange"
      />
    </div>
  </div>
</template>

<script setup>
import { ArrowDown } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { cancelTask, deleteTask, downloadUrl, listTasks } from '../api'

const router = useRouter()
const items = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const statusFilter = ref('')
const keyword = ref('')
const loading = ref(false)
const autoRefresh = ref(true)
const statusCounts = ref({})
let timer = null

// 展示用：pending + running 合并成“分析中”的数量
const runningCount = computed(() => (statusCounts.value.running || 0) + (statusCounts.value.pending || 0))

function statusText(status) {
  return { pending: '排队中', running: '分析中', completed: '已完成', failed: '失败', cancelled: '已取消' }[status] || status
}

function statusType(status) {
  return { pending: 'info', running: 'warning', completed: 'success', failed: 'danger', cancelled: 'info' }[status] || 'info'
}

async function cancel(row) {
  try {
    await ElMessageBox.confirm(
      `确定取消任务「${row.filename}」吗？<br>分析会立即停止，并删除该任务产生的全部数据，且无法恢复。`,
      '取消分析',
      { type: 'warning', dangerouslyUseHTMLString: true, confirmButtonText: '取消分析', cancelButtonText: '继续分析' }
    )
  } catch {
    return
  }
  try {
    await cancelTask(row.task_id)
    ElMessage.info('已请求取消，正在停止分析并清理数据…')
    setTimeout(() => load(false), 3000)
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function formatTime(value) {
  return (value || '').replace('T', ' ')
}

async function load(showLoading = true) {
  if (showLoading) loading.value = true
  try {
    const payload = await listTasks({
      page: page.value,
      pageSize: pageSize.value,
      status: statusFilter.value,
      q: keyword.value.trim()
    })
    items.value = payload.items
    total.value = payload.total
    const counts = payload.status_counts || {}
    statusCounts.value = {
      ...counts,
      all: Object.values(counts).reduce((sum, value) => sum + value, 0)
    }
    // 当前筛选下没有数据但页码超界时回退一页
    if (!items.value.length && page.value > 1) {
      page.value = Math.max(1, Math.ceil(payload.total / pageSize.value))
      await load(false)
    }
  } catch (err) {
    ElMessage.error(err.message)
  } finally {
    loading.value = false
  }
}

function reload() {
  page.value = 1
  load()
}

function onPageChange(value) {
  page.value = value
  load()
}

function onSizeChange(value) {
  pageSize.value = value
  page.value = 1
  load()
}

function go(name, row) {
  router.push({ name, params: { taskId: row.task_id } })
}

function onRowClick(row) {
  if (row.status === 'completed') go('result', row)
  else go('task', row)
}

function download(row, kind) {
  window.open(downloadUrl(row.task_id, kind), '_blank')
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(
      `确定删除任务「${row.filename}」吗？<br>任务记录、上传文件、中间结果、结果与报告都会被删除，且无法恢复。`,
      '删除任务',
      { type: 'warning', dangerouslyUseHTMLString: true, confirmButtonText: '删除', cancelButtonText: '取消' }
    )
  } catch {
    return
  }
  try {
    await deleteTask(row.task_id)
    ElMessage.success('任务已删除')
    // 删掉当前页最后一条时回退一页
    if (items.value.length === 1 && page.value > 1) page.value -= 1
    await load()
  } catch (err) {
    ElMessage.error(err.message)
  }
}

function startTimer() {
  stopTimer()
  // 只有存在进行中的任务时才轮询，避免无意义的请求
  if (!autoRefresh.value || runningCount.value === 0) return
  timer = setInterval(() => load(false), 3000)
}

function stopTimer() {
  if (timer) clearInterval(timer)
  timer = null
}

watch([autoRefresh, runningCount], startTimer)

onMounted(async () => {
  await load()
  startTimer()
})

onBeforeUnmount(stopTimer)
</script>

<style scoped>
.head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  flex-wrap: wrap;
}

.filters {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 16px;
}

.filter-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.pager {
  margin-top: 14px;
  justify-content: flex-end;
}

.expand {
  padding: 8px 16px;
  line-height: 1.9;
  font-size: 13px;
}

.expand .error {
  color: #dc2626;
}
</style>
