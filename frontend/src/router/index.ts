import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'
import TraceConsole from '@/views/TraceConsole.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 批次追踪承载空区间确认回执与真实页面的分账消费，使用专用控制台。
    { path: '/batch_tracker', name: 'batch-tracker', component: TraceConsole },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
