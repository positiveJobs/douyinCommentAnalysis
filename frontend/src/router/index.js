import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'home', component: () => import('../views/HomeView.vue') },
  { path: '/tasks', name: 'tasks', component: () => import('../views/TaskListView.vue') },
  { path: '/task/:taskId', name: 'task', component: () => import('../views/TaskView.vue') },
  { path: '/result/:taskId', name: 'result', component: () => import('../views/ResultView.vue') },
  { path: '/report/:taskId', name: 'report', component: () => import('../views/ReportView.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/' }
]

export default createRouter({
  history: createWebHistory(),
  routes
})
