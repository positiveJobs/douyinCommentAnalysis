<template>
  <div class="page">
    <div class="card head">
      <div>
        <p class="section-title">完整分析报告</p>
        <p class="muted">任务 ID：{{ taskId }}</p>
        <p v-if="error" class="error-text">{{ error }}</p>
      </div>
      <div class="head-actions">
        <el-button @click="$router.push({ name: 'result', params: { taskId } })">返回结果页</el-button>
        <el-button @click="openInNewTab">在新窗口打开</el-button>
        <el-button @click="download('html')">下载 HTML</el-button>
        <el-button @click="download('markdown')">下载 Markdown</el-button>
        <el-button @click="download('json')">下载 JSON</el-button>
        <el-button type="primary" @click="download('package')">打包下载</el-button>
      </div>
    </div>

    <div class="card report-card">
      <div v-if="error" class="error-box">
        <p>报告加载失败：{{ error }}</p>
        <el-button type="primary" @click="retry">重新加载</el-button>
        <el-button @click="openInNewTab">在新窗口打开</el-button>
      </div>
      <!-- 报告由后端直接渲染为 HTML：几千条评论的大报告也不会卡住页面 -->
      <iframe
        v-else
        class="report-frame"
        :src="reportSrc"
        @load="onLoaded"
        @error="onError"
      ></iframe>
      <div v-if="loading" class="loading-tip">正在加载报告…</div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { downloadUrl, reportHtmlUrl } from '../api'

const route = useRoute()
const taskId = route.params.taskId
const reportSrc = ref('')
const loading = ref(true)
const error = ref('')

function onLoaded() {
  loading.value = false
}

function onError() {
  loading.value = false
  error.value = '无法加载报告内容，请确认任务已完成'
}

function retry() {
  error.value = ''
  loading.value = true
  reportSrc.value = `${reportHtmlUrl(taskId)}?t=${Date.now()}`
}

function openInNewTab() {
  window.open(reportHtmlUrl(taskId), '_blank')
}

function download(kind) {
  window.open(downloadUrl(taskId, kind), '_blank')
}

onMounted(() => {
  reportSrc.value = reportHtmlUrl(taskId)
  // 兜底：某些浏览器缓存命中时 iframe 的 load 不触发
  setTimeout(() => {
    loading.value = false
  }, 4000)
})
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
  flex-wrap: wrap;
}

.report-card {
  position: relative;
  padding: 0;
  overflow: hidden;
}

.report-frame {
  width: 100%;
  height: calc(100vh - 220px);
  min-height: 620px;
  border: 0;
  display: block;
  background: #fff;
}

.loading-tip {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #6b7280;
  background: rgba(255, 255, 255, 0.85);
  font-size: 14px;
}

.error-box {
  padding: 40px 20px;
  text-align: center;
}

.error-text {
  color: #dc2626;
  font-size: 13px;
}
</style>
