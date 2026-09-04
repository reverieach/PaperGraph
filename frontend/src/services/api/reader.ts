import { apiClient } from './client'
import type { Paper } from '@/types'
type PaperReaderChatTurn = { role: string; content: string }
export type PaperReaderCitation = {
  marker: string
  page?: number | null
  page_start?: number | null
  page_end?: number | null
  evidence_id?: string
  section_path?: string[]
  source_type?: string
  snippet?: string
}
export type MemoryDraftEvidenceItem = {
  content: string
  evidence_turn_ids: number[]
}
export type UserMemoryCandidate = MemoryDraftEvidenceItem & {
  kind: 'preference' | 'research_goal'
  confidence: number
}
export type MemoryDraft = {
  id: string
  paper_id: number
  conversation_id: string
  from_turn_id: number
  to_turn_id: number
  status: string
  payload: {
    paper_summary: string
    key_findings: MemoryDraftEvidenceItem[]
    open_questions: MemoryDraftEvidenceItem[]
    research_decisions: MemoryDraftEvidenceItem[]
    user_memory_candidates: UserMemoryCandidate[]
  }
}
export type CommitMemoryItem = {
  kind:
    | 'reading_summary'
    | 'key_finding'
    | 'open_question'
    | 'research_decision'
    | 'preference'
    | 'research_goal'
  content: string
}
const PAPER_READER_CHAT_REQUEST_MS = 240000
export async function postPaperReaderOpening(
  paperId: number,
  conversationId?: string,
): Promise<{
  success: boolean
  opening: string
  conversation_id: string
  pdf_parsing?: boolean
  context_mode?: string
  degradation_flags?: string[]
}> {
  const response = await apiClient.post('/api/ai/paper-reader/opening', {
    paper_id: paperId,
    conversation_id: conversationId || undefined,
  })
  return response.data
}
export async function postPaperReaderChat(body: {
  paper_id: number
  conversation_id?: string
  // Kept optional for an older API contract. The backend uses persisted,
  // user/paper/conversation-scoped history rather than client supplied turns.
  messages?: PaperReaderChatTurn[]
  user_message: string
}): Promise<{
  success: boolean
  reply: string
  conversation_id: string
  pdf_parsing?: boolean
  context_mode?: string
  degradation_flags?: string[]
  related_papers?: Paper[]
  related_hints?: any[]
  kg_edges?: any[]
  citations?: PaperReaderCitation[]
}> {
  const response = await apiClient.post('/api/ai/paper-reader/chat', body, {
    timeout: PAPER_READER_CHAT_REQUEST_MS,
  })
  return response.data
}
export async function getPaperReaderHistory(paperId: number, limit = 200, conversationId?: string): Promise<{
  success: boolean
  paper_id: number
  conversation_id: string
  turns: { id: number; role: string; content: string; created_at: number }[]
}> {
  const response = await apiClient.get('/api/ai/paper-reader/history', {
    params: {
      paper_id: paperId,
      limit,
      conversation_id: conversationId || undefined,
    },
  })
  return response.data
}
export async function createMemoryDraft(
  paperId: number,
  conversationId: string,
): Promise<{ success: boolean; draft: MemoryDraft }> {
  const response = await apiClient.post(`/api/papers/${paperId}/memory-drafts`, {
    conversation_id: conversationId,
  }, {
    timeout: PAPER_READER_CHAT_REQUEST_MS,
  })
  return response.data
}
export async function commitMemoryDraft(
  draftId: string,
  body: {
    paper_items: CommitMemoryItem[]
    accepted_user_items: CommitMemoryItem[]
  },
  idempotencyKey: string,
): Promise<{ success: boolean; status: string; memories: unknown[] }> {
  const response = await apiClient.post(
    `/api/memory-drafts/${draftId}/commit`,
    body,
    { headers: { 'Idempotency-Key': idempotencyKey } },
  )
  return response.data
}
export async function cancelMemoryDraft(
  draftId: string,
): Promise<{ success: boolean; status: string }> {
  const response = await apiClient.post(`/api/memory-drafts/${draftId}/cancel`)
  return response.data
}
