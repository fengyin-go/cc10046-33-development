import { request } from './client'

export type TraceWindow = {
  start: string
  end: string
  index?: number
  kind?: string
  kind_text?: string
}

export type SplitResult = {
  executable: boolean
  reason: string
  reason_text: string
  start: string
  end: string
  windows: TraceWindow[]
  message?: string
  opened_round?: { round_id: string; reopened: boolean }
}

export type EmptyReceipt = {
  receipt_id: string
  round_id: string
  source: string
  window: { start: string; end: string }
  observed_rows: number
  status: string
  corrected_by?: string
  corrected_in_round?: string
  note?: string
  duplicate?: boolean
}

export type TracePage = {
  page_id: string
  ledger_id: string
  round_id: string
  source: string
  marker: string
  owner_window: TraceWindow | null
  first_valid_of_round: boolean
  front_correction: boolean
  corrected_receipts: string[]
  note?: string
  duplicate?: boolean
}

export type NextTask = {
  round_id: string
  source: string
  windows_total: number
  confirmed_empty: { start: string; end: string }[]
  covered_by_page: TraceWindow[]
  corrected_empty: { start: string; end: string; receipt_id: string; corrected_by?: string }[]
  pending_windows: TraceWindow[]
  next_from: string | null
  will_refetch: boolean
}

export type ReopenView = {
  round: { round_id: string; source: string; windows: TraceWindow[]; invalid_windows: TraceWindow[] } | null
  empty_receipts: EmptyReceipt[]
  pages: TracePage[]
  next_task: NextTask
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  if (!response.ok) {
    let detail = ''
    try {
      const errorBody = await response.json()
      detail = typeof errorBody.detail === 'object' && errorBody.detail
        ? errorBody.detail.message ?? JSON.stringify(errorBody.detail)
        : String(errorBody.detail ?? '')
    } catch {
      detail = ''
    }
    throw new Error(detail || `接口返回 ${response.status}`)
  }
  return (await response.json()) as T
}

async function getJson<T>(path: string): Promise<T> {
  const response = await request(path)
  if (!response.ok) throw new Error(`接口返回 ${response.status}`)
  return (await response.json()) as T
}

export function splitWindows(payload: {
  start: string
  end: string
  count: number
  round_id?: string
  source?: string
}): Promise<SplitResult> {
  return postJson('/api/trace/split-windows', payload)
}

export function openRound(payload: {
  round_id: string
  source: string
  windows: TraceWindow[]
  note?: string
}) {
  return postJson('/api/trace/rounds', payload)
}

export function confirmEmpty(payload: {
  round_id: string
  source: string
  start: string
  end: string
  observed_rows?: number
  note?: string
}): Promise<EmptyReceipt> {
  return postJson('/api/trace/empty-receipts', payload)
}

export function ingestPage(payload: {
  round_id: string
  source: string
  marker: string
  page_id: string
  note?: string
}): Promise<TracePage> {
  return postJson('/api/trace/pages', payload)
}

export function reopenRound(round_id: string, source: string): Promise<ReopenView> {
  return postJson('/api/trace/rounds/reopen', { round_id, source })
}

export function fetchNextTask(round_id: string, source: string): Promise<NextTask> {
  return postJson('/api/trace/next-task', { round_id, source })
}

export function fetchReceipts(round_id: string, source: string): Promise<{ items: EmptyReceipt[] }> {
  const query = new URLSearchParams({ source }).toString()
  return getJson(`/api/trace/rounds/${encodeURIComponent(round_id)}/receipts?${query}`)
}

export function fetchPages(round_id: string, source: string): Promise<{ items: TracePage[] }> {
  const query = new URLSearchParams({ source }).toString()
  return getJson(`/api/trace/rounds/${encodeURIComponent(round_id)}/pages?${query}`)
}
