<template>
  <section class="page trace-console" data-module="batch_tracker">
    <header class="page-head">
      <div>
        <h2>批次追踪 · 采集时间区间控制台</h2>
        <p class="page-desc">
          区分「无法执行的零宽/逆序区间」与「实际抓取零条的空区间」；空区间确认回执与真实页面分账消费，
          已确认无数据的区间后续任务不重取，边界页只确认一次。
        </p>
      </div>
    </header>

    <p v-if="message" class="trace-message" :class="messageKind">{{ message }}</p>

    <!-- ① 按时间切分 -->
    <article class="trace-card">
      <h3>① 按时间切分采集窗口</h3>
      <form class="trace-form" @submit.prevent="doSplit">
        <label><span>轮次标识</span><input v-model="splitForm.round_id" placeholder="如 R-1" required /></label>
        <label><span>来源</span><input v-model="splitForm.source" placeholder="如 src-a" required /></label>
        <label><span>起始时间</span><input v-model="splitForm.start" placeholder="08:00" required /></label>
        <label><span>截止时间</span><input v-model="splitForm.end" placeholder="12:00" required /></label>
        <label><span>切分数量</span><input v-model.number="splitForm.count" type="number" min="1" required /></label>
        <button class="btn primary" type="submit">切分并开轮</button>
      </form>
      <p v-if="splitResult" class="trace-message" :class="splitResult.executable ? 'ok' : 'warn'">
        <template v-if="splitResult.executable">
          正常区间，已切出 {{ splitResult.windows.length }} 个半开窗口
          <template v-if="splitResult.opened_round?.reopened">（该轮次已存在，为重新打开，历史账目保留）</template>
        </template>
        <template v-else>
          {{ splitResult.reason_text }}：{{ splitResult.message }}，未生成任何窗口与空回执
        </template>
      </p>
      <table v-if="splitResult?.executable" class="data-table">
        <thead><tr><th>序号</th><th>窗口（半开 [start, end)）</th><th>状态</th></tr></thead>
        <tbody>
          <tr v-for="window in splitResult.windows" :key="`${window.start}-${window.end}`">
            <td>{{ window.index }}</td>
            <td>{{ window.start }} ~ {{ window.end }}</td>
            <td>可执行</td>
          </tr>
        </tbody>
      </table>
    </article>

    <!-- ② 空回执 / 真实页消费 -->
    <article class="trace-card">
      <h3>② 消费空区间确认回执与真实页面</h3>
      <div class="trace-form">
        <label><span>轮次标识</span><input v-model="consumeForm.round_id" placeholder="R-1" /></label>
        <label><span>来源</span><input v-model="consumeForm.source" placeholder="src-a" /></label>
      </div>
      <div class="action-grid">
        <form class="sub-form" @submit.prevent="doConfirmEmpty">
          <h4>实际查到零条 → 登记空回执</h4>
          <label><span>窗口起</span><input v-model="consumeForm.start" placeholder="08:00" /></label>
          <label><span>窗口止</span><input v-model="consumeForm.end" placeholder="09:00" /></label>
          <button class="btn" type="submit">确认空区间</button>
        </form>
        <form class="sub-form" @submit.prevent="doIngestPage">
          <h4>收到真实页面 → 落账（重复送达幂等）</h4>
          <label><span>时间标记</span><input v-model="consumeForm.marker" placeholder="10:00" /></label>
          <label><span>页面标识</span><input v-model="consumeForm.page_id" placeholder="PG-1000" /></label>
          <button class="btn primary" type="submit">送页面</button>
        </form>
      </div>
    </article>

    <!-- ③ 重新打开批次追踪，分别回查 -->
    <article class="trace-card">
      <h3>③ 重新打开批次追踪（另一入口回查）</h3>
      <form class="trace-form" @submit.prevent="doReopen">
        <label><span>轮次标识</span><input v-model="lookupForm.round_id" placeholder="R-1" required /></label>
        <label><span>来源</span><input v-model="lookupForm.source" placeholder="src-a" required /></label>
        <button class="btn" type="submit">重新打开 / 回查</button>
      </form>

      <template v-if="reopenView">
        <p v-if="!reopenView.round" class="trace-message warn">该轮次不存在或尚未开轮。</p>
        <template v-else>
          <div class="stat-row">
            <article class="stat-card">
              <span class="stat-label">窗口总数</span>
              <strong class="stat-value">{{ reopenView.next_task.windows_total }}</strong>
            </article>
            <article class="stat-card">
              <span class="stat-label">已确认无数据（不重取）</span>
              <strong class="stat-value">{{ reopenView.next_task.confirmed_empty.length }}</strong>
            </article>
            <article class="stat-card">
              <span class="stat-label">已被真实页更正</span>
              <strong class="stat-value">{{ reopenView.next_task.corrected_empty.length }}</strong>
            </article>
            <article class="stat-card">
              <span class="stat-label">真实页覆盖</span>
              <strong class="stat-value">{{ reopenView.next_task.covered_by_page.length }}</strong>
            </article>
          </div>

          <h4>空区间确认回执</h4>
          <table class="data-table">
            <thead><tr><th>回执标识</th><th>窗口</th><th>观测行数</th><th>状态</th><th>更正来源</th></tr></thead>
            <tbody>
              <tr v-for="receipt in reopenView.empty_receipts" :key="receipt.receipt_id">
                <td>{{ receipt.receipt_id }}</td>
                <td>{{ receipt.window.start }} ~ {{ receipt.window.end }}</td>
                <td>{{ receipt.observed_rows }}</td>
                <td>{{ receipt.status }}</td>
                <td>{{ receipt.corrected_by ? `${receipt.corrected_by}（来自 ${receipt.corrected_in_round}）` : '—' }}</td>
              </tr>
              <tr v-if="!reopenView.empty_receipts.length">
                <td colspan="5" class="empty-state">暂无空回执</td>
              </tr>
            </tbody>
          </table>

          <h4>已落库真实页面</h4>
          <table class="data-table">
            <thead><tr><th>页面标识</th><th>时间标记</th><th>归属窗口</th><th>角色判定</th></tr></thead>
            <tbody>
              <tr v-for="page in reopenView.pages" :key="page.ledger_id">
                <td>{{ page.page_id }}</td>
                <td>{{ page.marker }}</td>
                <td>{{ page.owner_window ? `${page.owner_window.start} ~ ${page.owner_window.end}` : '轮次窗口外（迟到）' }}</td>
                <td>
                  <span v-if="page.front_correction" class="tag tag-correct">前轮更正</span>
                  <span v-if="page.first_valid_of_round" class="tag tag-first">后轮首条有效页</span>
                  <span v-if="!page.front_correction && !page.first_valid_of_round">普通页</span>
                </td>
              </tr>
              <tr v-if="!reopenView.pages.length">
                <td colspan="4" class="empty-state">暂无真实页面</td>
              </tr>
            </tbody>
          </table>

          <h4>下次任务还缺什么</h4>
          <p class="trace-message" :class="reopenView.next_task.will_refetch ? 'warn' : 'ok'">
            <template v-if="reopenView.next_task.will_refetch">
              仍需重取，下一个起点：{{ reopenView.next_task.next_from }}；
              待办窗口：{{ describeWindows(reopenView.next_task.pending_windows) }}
            </template>
            <template v-else>
              无缺口，水位推进至 {{ reopenView.next_task.next_from ?? '—' }}，已确认无数据区间不会重取
            </template>
          </p>
        </template>
      </template>
    </article>
  </section>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'

import {
  confirmEmpty,
  ingestPage,
  reopenRound,
  splitWindows,
  type ReopenView,
  type SplitResult,
} from '@/api/trace-service'

const splitForm = reactive({ round_id: '', source: '', start: '', end: '', count: 2 })
const consumeForm = reactive({ round_id: '', source: '', start: '', end: '', marker: '', page_id: '' })
const lookupForm = reactive({ round_id: '', source: '' })

const splitResult = ref<SplitResult | null>(null)
const reopenView = ref<ReopenView | null>(null)
const message = ref('')
const messageKind = ref('ok')

function flash(text: string, kind: 'ok' | 'warn' | 'error' = 'ok') {
  message.value = text
  messageKind.value = kind
}

function describeWindows(windows: { start: string; end: string }[]): string {
  return windows.map((window) => `${window.start}~${window.end}`).join('、') || '无'
}

async function doSplit() {
  try {
    splitResult.value = await splitWindows({ ...splitForm })
    if (!splitResult.value.executable) {
      flash(splitResult.value.message ?? '区间无法执行', 'warn')
    } else {
      flash('窗口切分并开轮成功', 'ok')
    }
  } catch (error) {
    flash(error instanceof Error ? error.message : '切分失败', 'error')
  }
}

async function doConfirmEmpty() {
  try {
    const receipt = await confirmEmpty({
      round_id: consumeForm.round_id,
      source: consumeForm.source,
      start: consumeForm.start,
      end: consumeForm.end,
    })
    flash(
      receipt.duplicate ? `回执 ${receipt.receipt_id} 已存在，重复确认未重复登记` : `空回执 ${receipt.receipt_id} 已登记（${receipt.status}）`,
      'ok',
    )
  } catch (error) {
    flash(error instanceof Error ? error.message : '空区间确认被拒绝', 'error')
  }
}

async function doIngestPage() {
  try {
    const page = await ingestPage({
      round_id: consumeForm.round_id,
      source: consumeForm.source,
      marker: consumeForm.marker,
      page_id: consumeForm.page_id,
    })
    const roles: string[] = []
    if (page.duplicate) roles.push('重复送达，幂等返回未重复落库')
    if (page.front_correction) roles.push(`前轮更正，已更正回执 ${page.corrected_receipts.join('、')}`)
    if (page.first_valid_of_round) roles.push('后轮首条有效页')
    flash(roles.length ? roles.join('；') : `页面 ${page.page_id} 已落账`, page.duplicate ? 'warn' : 'ok')
  } catch (error) {
    flash(error instanceof Error ? error.message : '页面落账失败', 'error')
  }
}

async function doReopen() {
  try {
    reopenView.value = await reopenRound(lookupForm.round_id, lookupForm.source)
    flash('批次追踪已重新打开，空回执与真实页面分别回查', 'ok')
  } catch (error) {
    flash(error instanceof Error ? error.message : '重新打开失败', 'error')
  }
}
</script>

<style scoped>
.trace-console { display: flex; flex-direction: column; gap: 16px; }
.trace-card { background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
.trace-card h3 { margin: 0 0 12px; font-size: 15px; }
.trace-card h4 { margin: 14px 0 8px; font-size: 13px; color: var(--muted); }
.trace-form { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }
.trace-form label, .sub-form label { display: flex; flex-direction: column; font-size: 12px; color: var(--muted); gap: 4px; }
.trace-form input, .sub-form input { border: 1px solid var(--border); border-radius: 6px; padding: 7px 9px; min-width: 130px; }
.action-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 12px; }
.sub-form { border: 1px dashed var(--border); border-radius: 8px; padding: 12px; display: flex; flex-direction: column; gap: 8px; align-items: flex-start; }
.trace-message { font-size: 13px; padding: 8px 10px; border-radius: 6px; }
.trace-message.ok { background: #ecfdf3; color: #027a48; }
.trace-message.warn { background: #fffaeb; color: #b54708; }
.trace-message.error { background: #fef3f2; color: #b42318; }
.tag { display: inline-block; border-radius: 999px; padding: 2px 8px; font-size: 12px; margin-right: 6px; }
.tag-correct { background: #fef3f2; color: #b42318; border: 1px solid #fecdca; }
.tag-first { background: #eff8ff; color: #175cd3; border: 1px solid #b2ddff; }
</style>
