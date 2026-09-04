import { apiClient } from './client'

export type UserMemoryKind = 'preference' | 'research_goal'

export interface MemoryItem {
  id: string
  scope_type: 'paper' | 'conversation' | 'user'
  scope_id: string
  kind: string
  content: string
  source_type: string
  confirmed_by_user: boolean
  status: string
  metadata: Record<string, unknown>
  created_at: number
  updated_at: number
}

export async function getUserMemories(limit = 200): Promise<MemoryItem[]> {
  const response = await apiClient.get<{
    success: boolean
    count: number
    items: MemoryItem[]
  }>('/api/memory/user', { params: { limit } })
  return response.data.items
}

export async function createUserMemory(body: {
  kind: UserMemoryKind
  content: string
}): Promise<{ item: MemoryItem; created: boolean }> {
  const response = await apiClient.post<{
    success: boolean
    item: MemoryItem
    created: boolean
  }>('/api/memory/user', body)
  return {
    item: response.data.item,
    created: response.data.created,
  }
}

export async function deleteMemory(memoryId: string): Promise<void> {
  await apiClient.delete(`/api/memories/${encodeURIComponent(memoryId)}`)
}
