<template>
  <el-drawer :model-value="visible" size="720px" :title="title" @update:model-value="$emit('update:visible', $event)">
    <div v-if="detail" v-loading="loading">
      <div class="card">
        <p class="section-title">{{ detail.viewpoint_id }} {{ detail.name }}</p>
        <p class="muted">{{ detail.description }}</p>
        <p>
          <span class="muted">评论数量：</span><b>{{ detail.comment_count }}</b> 条
          <span class="muted" style="margin-left: 12px">占有效评论：</span><b>{{ detail.percentage }}%</b>
          <span class="muted" style="margin-left: 12px">立场倾向：</span><b>{{ stanceText(detail.stance) }}</b>
        </p>
      </div>

      <div class="card">
        <p class="section-title">主要理由</p>
        <el-table :data="detail.reasons" size="small" empty-text="该观点下没有识别到明确理由">
          <el-table-column prop="name" label="理由" min-width="220" />
          <el-table-column prop="comment_count" label="评论数" width="90" sortable />
          <el-table-column prop="percentage" label="占该观点" width="100">
            <template #default="{ row }">{{ row.percentage }}%</template>
          </el-table-column>
        </el-table>
      </div>

      <div class="card">
        <p class="section-title">代表性评论</p>
        <el-empty v-if="!detail.representative_comments.length" description="暂无代表性评论" :image-size="60" />
        <blockquote v-for="(comment, index) in detail.representative_comments" :key="index" class="quote">
          {{ comment }}
        </blockquote>
      </div>

      <div class="card">
        <p class="section-title">
          该观点对应的原始评论（共 {{ comments.total }} 条）
        </p>
        <el-table :data="comments.items" size="small" v-loading="loadingComments">
          <el-table-column prop="comment_id" label="comment_id" width="170" />
          <el-table-column prop="content" label="原始评论" min-width="300" />
          <el-table-column prop="like_count" label="点赞" width="70" />
        </el-table>
        <el-pagination
          class="pager"
          layout="prev, pager, next, total"
          :total="comments.total"
          :page-size="comments.page_size || 20"
          :current-page="comments.page || 1"
          @current-change="loadComments"
        />
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

import { getViewpoint, getViewpointComments } from '../api'

const props = defineProps({
  visible: { type: Boolean, default: false },
  taskId: { type: String, required: true },
  viewpointId: { type: String, default: null }
})

defineEmits(['update:visible'])

const detail = ref(null)
const loading = ref(false)
const loadingComments = ref(false)
const comments = ref({ total: 0, page: 1, page_size: 20, items: [] })

const title = computed(() => (props.viewpointId ? `观点详情 ${props.viewpointId}` : '观点详情'))

const STANCES = { support: '支持', oppose: '反对', conditional: '有条件', neutral: '中立', unclear: '不明' }
function stanceText(stance) {
  return STANCES[stance] || stance || '不明'
}

async function loadComments(page = 1) {
  if (!props.viewpointId) return
  loadingComments.value = true
  try {
    comments.value = await getViewpointComments(props.taskId, props.viewpointId, page, 20)
  } finally {
    loadingComments.value = false
  }
}

async function load() {
  if (!props.visible || !props.viewpointId) return
  loading.value = true
  detail.value = null
  try {
    detail.value = await getViewpoint(props.taskId, props.viewpointId)
    await loadComments(1)
  } finally {
    loading.value = false
  }
}

watch(() => [props.visible, props.viewpointId], load)
</script>

<style scoped>
.quote {
  margin: 8px 0;
  padding: 10px 12px;
  background: #f8fafc;
  border-left: 3px solid #93c5fd;
  border-radius: 4px;
  font-size: 13px;
  line-height: 1.7;
}

.pager {
  margin-top: 12px;
  justify-content: flex-end;
}
</style>
