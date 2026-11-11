<template>
  <el-header class="app-header">
    <div class="header-inner">
      <router-link to="/" class="brand">评论观点智能分析</router-link>
      <nav class="nav">
        <router-link to="/" class="nav-link">上传分析</router-link>
        <router-link to="/tasks" class="nav-link">任务记录</router-link>
      </nav>
      <span class="muted tagline">上传评论 JSON，自动提取观点、理由与代表性评论</span>
    </div>
  </el-header>
  <router-view />
</template>

<style scoped>
.app-header {
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  padding: 0;
}

.header-inner {
  max-width: 1180px;
  margin: 0 auto;
  padding: 14px 16px;
  display: flex;
  align-items: baseline;
  gap: 16px;
}

.brand {
  font-size: 18px;
  font-weight: 600;
  color: #1f2329;
  text-decoration: none;
  white-space: nowrap;
}

.nav {
  display: flex;
  gap: 12px;
}

.nav-link {
  color: #4b5563;
  text-decoration: none;
  font-size: 14px;
  padding: 4px 2px;
  white-space: nowrap;
}

.nav-link.router-link-exact-active {
  color: #2563eb;
  font-weight: 600;
  border-bottom: 2px solid #2563eb;
}

.tagline {
  margin-left: auto;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (max-width: 900px) {
  .tagline {
    display: none;
  }
}
</style>
