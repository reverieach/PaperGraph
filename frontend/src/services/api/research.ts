import { apiClient } from './client'

export interface ResearchPaper {
  id: number
  title: string
  abstract: string
  year: number | null
  journal: string
  category: string
  authors: string[]
}

export interface ResearchTurn {
  id: number
  role: 'user' | 'assistant'
  content: string
  metadata: ResearchTurnMetadata
  created_at: number
}

export interface ResearchCitation {
  evidence_id: string
  marker: string
  paper_id: number
  paper_title: string
  document_version_id: string
  chunk_uid: string
  content_type: string
  page: number | null
  page_start: number | null
  page_end: number | null
  section_path: string[]
  snippet: string
}

export interface ResearchTurnMetadata {
  context_mode?: string
  citations?: ResearchCitation[]
  degradation_reasons?: string[]
  [key: string]: unknown
}

export interface ResearchSession {
  id: string
  title: string
  papers: ResearchPaper[]
  turns: ResearchTurn[]
  created_at: number
  updated_at: number
}

export async function createResearchSession(body: {
  paper_ids: number[]
  title?: string
}): Promise<ResearchSession> {
  const response = await apiClient.post<{
    success: boolean
    session: ResearchSession
  }>('/api/research/sessions', body)
  return response.data.session
}

export async function getResearchSession(
  sessionId: string,
): Promise<ResearchSession> {
  const response = await apiClient.get<{
    success: boolean
    session: ResearchSession
  }>(`/api/research/sessions/${encodeURIComponent(sessionId)}`)
  return response.data.session
}

export async function sendResearchMessage(
  sessionId: string,
  userMessage: string,
): Promise<{
  reply: string
  turns: ResearchTurn[]
  context_mode: string
  citations: ResearchCitation[]
  degradation_flags: string[]
}> {
  const response = await apiClient.post<{
    success: boolean
    reply: string
    turns: ResearchTurn[]
    context_mode: string
    citations: ResearchCitation[]
    degradation_flags: string[]
  }>(
    `/api/research/sessions/${encodeURIComponent(sessionId)}/chat`,
    { user_message: userMessage },
  )
  return response.data
}
