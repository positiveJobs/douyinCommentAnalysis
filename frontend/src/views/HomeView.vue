<template>
  <div class="page">
    <div class="card hero">
      <h1>评论智能分析</h1>
      <p class="muted">
        上传评论 JSON 文件，系统会自动完成数据清洗、LLM 语义分析、Embedding 聚类、观点归并、理由归纳与统计，
        并生成可下载的分析报告。
      </p>
      <p class="muted">
        支持抖音 / 小红书 / B站 / 微博 / 电商评价 / 问卷文本等评论数据，只要能转换成
        <code>[{ "comment_id": "...", "content": "..." }]</code> 这类结构即可。
      </p>
    </div>

    <div class="card">
      <el-upload
        drag
        :auto-upload="false"
        :limit="1"
        accept=".json"
        :on-change="onFileChange"
        :on-remove="onFileRemove"
        :file-list="fileList"
      >
        <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
        <div class="el-upload__text">把评论 JSON 文件拖到这里，或<em>点击选择文件</em></div>
        <template #tip>
          <div class="el-upload__tip">仅支持 .json，最大 100 MB</div>
        </template>
      </el-upload>

      <div v-if="file" class="file-info">
        <div><span class="muted">文件名：</span>{{ file.name }}</div>
        <div><span class="muted">文件大小：</span>{{ formatSize(file.size) }}</div>
        <div v-if="preflightResult">
          <span class="muted">评论数量：</span>{{ preflightResult.comment_count }} 条
          <el-tag v-if="preflightResult.topic" type="success" size="small" style="margin-left: 8px">
            文件内已包含主题
          </el-tag>
        </div>
        <div v-if="preflightError" class="error-text">{{ preflightError }}</div>
      </div>

      <el-form label-width="120px" class="topic-form">
        <el-form-item label="讨论主题（可选）">
          <el-input
            v-model="topic"
            type="textarea"
            :rows="2"
            maxlength="300"
            show-word-limit
            placeholder="例如：我今年17岁，明年成年就想出去工作了，该直接进厂还是慢慢找适合自己的工作？不填则由系统根据评论内容判断"
          />
        </el-form-item>
      </el-form>

      <div class="actions">
        <el-button type="primary" size="large" :loading="submitting" :disabled="!file" @click="submit">
          开始分析
        </el-button>
        <span v-if="error" class="error-text">{{ error }}</span>
      </div>
    </div>

    <div class="card">
      <div class="recent-head">
        <p class="section-title">最近的分析任务</p>
        <el-button link type="primary" @click="$router.push({ name: 'tasks' })">查看全部任务记录 →</el-button>
      </div>
      <el-table :data="tasks" size="small" v-loading="loadingTasks" empty-text="暂无任务">
        <el-table-column prop="filename" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="160">
          <template #default="{ row }">
            <el-progress :percentage="Math.round(row.progress || 0)" :stroke-width="8" />
          </template>
        </el-table-column>
        <el-table-column prop="comment_count" label="评论数" width="90" />
        <el-table-column prop="viewpoint_count" label="观点数" width="90" />
        <el-table-column prop="created_at" label="创建时间" width="170" />
        <el-table-column label="操作" width="150">
          <template #default="{ row }">
            <el-button v-if="row.status === 'completed'" link type="primary" @click="openTask(row)">
              查看结果
            </el-button>
            <el-button v-else link type="primary" @click="openTask(row)">查看</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <p class="muted env-line" v-if="health">
      向量设备：<b>{{ health.embedding?.last_used_label || health.embedding?.will_use_label || '—' }}</b>
      <span v-if="health.embedding">（可用后端 {{ (health.embedding.available_providers || []).join(' / ') }}）</span>
      · LLM 缓存 {{ health.cache?.llm_cache_entries || 0 }} 条
      · 评论级缓存 {{ health.cache?.extraction_cache_entries || 0 }} 条
      · 向量缓存 {{ health.cache?.embedding_cache_entries || 0 }} 条
    </p>
  </div>
</template>

<script setup>
import { UploadFilled } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { createTask, getHealth, listTasks, preflight } from '../api'

const router = useRouter()
const file = ref(null)
const fileList = ref([])
const preflightResult = ref(null)
const preflightError = ref('')
const topic = ref('')
const submitting = ref(false)
const error = ref('')
const tasks = ref([])
const loadingTasks = ref(false)
const health = ref(null)

function statusText(status) {
  return { pending: '排队中', running: '分析中', completed: '已完成', failed: '失败' }[status] || status
}

function statusType(status) {
  return { pending: 'info', running: 'warning', completed: 'success', failed: 'danger' }[status] || 'info'
}

function formatSize(bytes) {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let index = 0
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024
    index += 1
  }
  return `${value.toFixed(1)} ${units[index]}`
}

async function onFileChange(uploadFile) {
  file.value = uploadFile.raw
  fileList.value = [uploadFile]
  preflightResult.value = null
  preflightError.value = ''
  error.value = ''
  try {
    const result = await preflight(file.value)
    preflightResult.value = result
    if (!topic.value && result.topic) topic.value = result.topic
  } catch (err) {
    preflightError.value = err.message
  }
}

function onFileRemove() {
  file.value = null
  fileList.value = []
  preflightResult.value = null
}

async function submit() {
  if (!file.value) return
  submitting.value = true
  error.value = ''
  try {
    const result = await createTask(file.value, topic.value.trim() || undefined)
    ElMessage.success('任务已创建，正在分析')
    router.push({ name: 'task', params: { taskId: result.task_id } })
  } catch (err) {
    error.value = err.message
    ElMessage.error(err.message)
  } finally {
    submitting.value = false
  }
}

async function loadTasks() {
  loadingTasks.value = true
  try {
    const payload = await listTasks({ page: 1, pageSize: 5 })
    tasks.value = payload.items
  } catch (err) {
    // 列表加载失败不阻塞主流程
  } finally {
    loadingTasks.value = false
  }
}

function openTask(row) {
  if (row.status === 'completed') {
    router.push({ name: 'result', params: { taskId: row.task_id } })
  } else {
    router.push({ name: 'task', params: { taskId: row.task_id } })
  }
}

onMounted(loadTasks)

onMounted(async () => {
  try {
    health.value = await getHealth()
  } catch (err) {
    // 健康信息只用于展示，失败不影响主流程
  }
})
</script>

<style scoped>
.hero h1 {
  margin: 0 0 8px;
  font-size: 24px;
}

.file-info {
  margin-top: 16px;
  line-height: 1.9;
  font-size: 14px;
}

.topic-form {
  margin-top: 16px;
}

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 8px;
}

.error-text {
  color: #dc2626;
  font-size: 13px;
}

.recent-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.env-line {
  text-align: center;
  margin-top: 8px;
}
</style>
