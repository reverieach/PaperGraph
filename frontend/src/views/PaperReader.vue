<template>
  <div class="paper-reader">
    <div class="paper-reader__toolbar">
      <a-space>
        <a-button type="link" @click="backToLibrary">← 返回文献库</a-button>
      </a-space>
      <span v-if="paper" class="paper-reader__title">{{ paper.title }}</span>
      <span v-else class="paper-reader__title paper-reader__title--placeholder">文献阅读</span>
      <a-space v-if="paper" class="paper-reader__toolbar-actions">
        <a-button
          size="small"
          :loading="memoryDraftLoading"
          :disabled="sending || openingLoading || messages.length === 0"
          @click="openMemoryDraft"
        >
          总结本次阅读
        </a-button>
        <a-dropdown @click.stop>
          <a-button size="small" type="text"><CopyOutlined /> 复制引用</a-button>
          <template #overlay>
            <a-menu @click="onCopyCitation">
              <a-menu-item key="bibtex">BibTeX</a-menu-item>
              <a-menu-item key="apa">APA</a-menu-item>
              <a-menu-item key="plain">纯文本</a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
      </a-space>
    </div>
    <div v-if="loadError" class="paper-reader__err">{{ loadError }}</div>
    <div v-if="ingestNotice" class="paper-reader__notice paper-reader__ingest-notice">
      <span>{{ ingestNotice }}</span>
      <a-button
        v-if="canRetryIngest"
        size="small"
        :loading="ingestRetrying"
        @click="retryIngest"
      >
        重新入库
      </a-button>
    </div>
    <div ref="splitRef" class="paper-reader__split">
      <div class="paper-reader__pane paper-reader__pane--pdf" :style="leftPaneStyle">
        <PdfJsViewer
          v-if="paperId != null && pdfSrc"
          ref="pdfViewerRef"
          :src="pdfSrc"
          @loaded="onPdfLoaded"
          @error="onPdfError"
          class="pdf-js-viewer-wrapper"
        />
        <div v-else class="paper-reader__pdf-placeholder">
          <a-spin v-if="loadingPaper" tip="正在加载文献信息…" />
          <a-empty v-else-if="loadError" description="PDF 加载失败">
            <template #description>
              <p style="color: var(--pg-text-secondary); margin-bottom: 12px;">{{ loadError }}</p>
            </template>
            <a-button type="primary" @click="retryLoadPaper">重新加载</a-button>
          </a-empty>
          <a-empty v-else description="该文献尚无本地 PDF">
            <template #description>
              <p style="color: var(--pg-text-secondary); margin-bottom: 12px;">
                可基于摘要与助手对话；可回检索/文献库重新保存以重试下载。
              </p>
            </template>
            <a-button type="link" @click="backToLibrary">返回文献库</a-button>
          </a-empty>
        </div>
      </div>
      <div
        ref="dividerRef"
        class="paper-reader__divider"
        role="separator"
        aria-label="Resize"
        @pointerdown="onDividerPointerDown"
      />
      <div class="paper-reader__pane paper-reader__pane--chat" :style="rightPaneStyle">
        <div
          ref="scrollRef"
          class="paper-reader__messages"
          @wheel.stop="onChatWheel"
          @scroll.stop
        >
          <div v-if="(loadingPaper || openingLoading) && messages.length === 0" class="paper-reader__msg paper-reader__msg--assistant">
            <div class="paper-reader__avatar paper-reader__avatar--assistant">
              <RobotOutlined />
            </div>
            <div class="paper-reader__bubble paper-reader__bubble--assistant">
              <div class="paper-reader__msg-role">论文阅读助手</div>
              <a-skeleton active :paragraph="{ rows: 3 }" :title="{ width: '60%' }" />
            </div>
          </div>
          <div
            v-for="(m, i) in messages"
            :key="i"
            class="paper-reader__msg"
            :class="'paper-reader__msg--' + m.role"
          >
            <div v-if="m.role === 'assistant'" class="paper-reader__avatar paper-reader__avatar--assistant">
              <RobotOutlined />
            </div>
            <div v-if="m.role === 'user'" class="paper-reader__bubble paper-reader__bubble--user">
              <div class="paper-reader__msg-body">{{ m.content }}</div>
            </div>
            <div v-else class="paper-reader__bubble paper-reader__bubble--assistant">
              <div class="paper-reader__msg-role">论文阅读助手</div>
              <div class="paper-reader__msg-body" v-html="renderMarkdown(normalizeAssistantText(m.content))"></div>
              <div v-if="m.citations && m.citations.length" class="paper-reader__citations">
                <span class="paper-reader__citations-title">引用锚点</span>
                <div class="paper-reader__citations-list">
                  <button
                    v-for="(c, ci) in m.citations"
                    :key="ci"
                    type="button"
                    class="paper-reader__citation-chip"
                    :class="{ 'paper-reader__citation-chip--static': !c.page }"
                    :disabled="!c.page"
                    :title="c.snippet || (c.page ? `跳转到第 ${c.page} 页` : '当前证据没有可跳转的 PDF 页码')"
                    @click.stop="gotoCitationPage(c.page)"
                  >
                    {{ c.marker }}<template v-if="c.page"> · p{{ c.page }}</template>
                  </button>
                </div>
              </div>
              <div
                v-if="shouldShowContextStatus(m.context_mode, m.degradation_flags)"
                class="paper-reader__context-status"
              >
                {{ contextModeLabel(m.context_mode) }}
                <template v-if="m.degradation_flags && m.degradation_flags.length">
                  · {{ m.degradation_flags.length }} 项降级保护已生效
                </template>
              </div>
              <div v-if="m.related_papers && m.related_papers.length" class="paper-reader__related">
                <div class="paper-reader__related-title">推荐论文</div>
                <ul class="paper-reader__related-cards">
                  <li v-for="(item, index) in m.related_papers" :key="index" class="paper-reader__related-card">
                    <div class="paper-reader__related-card-head">
                      <span class="paper-reader__related-idx">{{ index + 1 }}.</span>
                      <a
                        v-if="paperExternalUrl(item)"
                        class="paper-reader__related-title-link"
                        :href="paperExternalUrl(item)!"
                        target="_blank"
                        rel="noopener noreferrer"
                        @click.stop
                      >
                        {{ item.title || '（无标题）' }}
                      </a>
                      <span v-else class="paper-reader__related-title-link paper-reader__related-title-link--text">
                        {{ item.title || '（无标题）' }}
                      </span>
                    </div>
                    <div v-if="relatedPaperMetaLine(item)" class="paper-reader__related-card-meta">
                      {{ relatedPaperMetaLine(item) }}
                    </div>
                    <div class="paper-reader__related-card-actions">
                      <a-button
                        v-if="paperExternalUrl(item)"
                        type="link"
                        size="small"
                        class="paper-reader__related-act"
                        :href="paperExternalUrl(item)!"
                        target="_blank"
                        rel="noopener noreferrer"
                        @click.stop
                      >
                        打开链接
                      </a-button>
                      <a-button
                        type="primary"
                        size="small"
                        ghost
                        class="paper-reader__related-act"
                        @click.stop="saveRelatedPaperToLibrary(item)"
                      >
                        保存到库
                      </a-button>
                    </div>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </div>
        <div class="paper-reader__input">
          <a-textarea
            :key="inputKey"
            v-model:value="draft"
            :rows="1"
            :auto-size="{ minRows: 1, maxRows: 6 }"
            :placeholder="openingLoading ? '正在准备论文导读…' : '基于当前文献提问…'"
            :disabled="sending || openingLoading"
            @compositionstart="composing = true"
            @compositionend="composing = false"
            @press-enter.exact.prevent="send"
          />
          <a-button type="primary" :loading="sending" :disabled="openingLoading || !draft.trim()" aria-label="发送消息" @click="send">发送</a-button>
        </div>
      </div>
    </div>
    <a-modal
      v-model:open="memoryDraftOpen"
      title="保存阅读记忆"
      width="720px"
      :closable="!memoryCommitLoading && !memoryCancelLoading"
      :mask-closable="false"
      @cancel="discardMemoryDraft"
    >
      <div class="paper-reader__memory-modal-body">
        <div v-if="paperMemoryItems.length" class="paper-reader__memory-section">
          <h4>当前论文记忆</h4>
          <div v-for="(item, index) in paperMemoryItems" :key="`paper-${index}`" class="paper-reader__memory-item">
            <a-checkbox v-model:checked="item.selected">保存这份论文阅读总结</a-checkbox>
            <a-textarea v-model:value="item.content" :auto-size="{ minRows: 7, maxRows: 12 }" />
          </div>
        </div>
        <div v-if="userMemoryItems.length" class="paper-reader__memory-section">
          <h4>长期用户记忆候选</h4>
          <div v-for="(item, index) in userMemoryItems" :key="`user-${index}`" class="paper-reader__memory-item">
            <a-checkbox v-model:checked="item.selected">
              {{ memoryKindLabel(item.kind) }} · 置信度 {{ Math.round(item.confidence * 100) }}%
            </a-checkbox>
            <a-textarea v-model:value="item.content" :auto-size="{ minRows: 2, maxRows: 5 }" />
          </div>
        </div>
      </div>
      <template #footer>
        <a-button :loading="memoryCancelLoading" :disabled="memoryCommitLoading" @click="discardMemoryDraft">
          放弃本次草稿
        </a-button>
        <a-button
          type="primary"
          :loading="memoryCommitLoading"
          :disabled="memoryCancelLoading || !hasSelectedMemory"
          @click="commitSelectedMemory"
        >
          确认保存
        </a-button>
      </template>
    </a-modal>
  </div>
</template>
<script setup lang="ts">
import { ref, computed, watch, nextTick, onBeforeUnmount, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { RobotOutlined, CopyOutlined } from '@ant-design/icons-vue'
import {
  getPaper,
  getLibraryPdfBlob,
  postPaperReaderOpening,
  postPaperReaderChat,
  getPaperReaderHistory,
  createMemoryDraft,
  commitMemoryDraft,
  cancelMemoryDraft,
  postReadingLog,
  savePapers,
  enqueuePaperIngest,
  getPaperIngestStatus,
} from '@/services/api'
import type { PaperIngestStatus } from '@/services/api/papers'
import type { CommitMemoryItem, PaperReaderCitation } from '@/services/api/reader'
import type { Paper } from '@/types'
import PdfJsViewer from '@/components/PdfJsViewer.vue'
import { renderMarkdown } from '@/utils/markdown'
const route = useRoute()
const router = useRouter()
const paper = ref<Paper | null>(null)
const loadingPaper = ref(true)
const loadError = ref('')
const pdfParsing = ref(false)
const ingestStatus = ref<PaperIngestStatus | null>(null)
const ingestRetrying = ref(false)
let ingestPollTimer: ReturnType<typeof setTimeout> | null = null
const pdfObjectUrl = ref('')
const pdfViewerRef = ref<InstanceType<typeof PdfJsViewer> | null>(null)
const messages = ref<
  {
    role: 'user' | 'assistant'
    content: string
    related_papers?: Paper[]
    citations?: PaperReaderCitation[]
    context_mode?: string
    degradation_flags?: string[]
  }[]
>([])
const draft = ref('')
const sending = ref(false)
const conversationId = ref('')
type EditableMemoryItem = CommitMemoryItem & {
  selected: boolean
  confidence: number
}
const memoryDraftOpen = ref(false)
const memoryDraftLoading = ref(false)
const memoryCommitLoading = ref(false)
const memoryCancelLoading = ref(false)
const memoryDraftId = ref('')
const paperMemoryItems = ref<EditableMemoryItem[]>([])
const userMemoryItems = ref<EditableMemoryItem[]>([])
const hasSelectedMemory = computed(() =>
  [...paperMemoryItems.value, ...userMemoryItems.value]
    .some((item) => item.selected && item.content.trim()),
)
const composing = ref(false)
const inputKey = ref(0)
const scrollRef = ref<HTMLElement | null>(null)
const splitRef = ref<HTMLElement | null>(null)
const dividerRef = ref<HTMLElement | null>(null)
const leftWidthPx = ref<number | null>(null)
const dragging = ref(false)
const dragPointerId = ref<number | null>(null)
const rafPending = ref(false)
const lastClientX = ref<number | null>(null)
const readingSession = ref<{ paperId: number; startedAtMs: number } | null>(null)
const normalizeAssistantText = (s: string): string => {
  const raw = String(s || '')
  if (raw.includes('```')) return raw.replace(/\r\n/g, '\n')
  return raw
    .replace(/\r\n/g, '\n')
    .replace(/^[ \t]+$/gm, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
const paperId = computed(() => {
  const raw = route.params.id
  const n = typeof raw === 'string' ? parseInt(raw, 10) : Array.isArray(raw) ? parseInt(raw[0], 10) : NaN
  return Number.isFinite(n) && n > 0 ? n : null
})
const hasLocalPdfForViewer = computed(
  () => !!(paper.value?.local_pdf_path && String(paper.value.local_pdf_path).trim())
)
const pdfSrc = computed(() => {
  return hasLocalPdfForViewer.value ? pdfObjectUrl.value : ''
})
const ingestJobStatus = computed(() => ingestStatus.value?.latest_job?.status || '')
const terminalIngestErrorCodes = new Set([
  'PDF_FILE_MISSING',
  'PDF_HASH_FAILED',
  'PDF_ENCRYPTED',
  'PDF_INVALID',
  'QUALITY_GATE_FAILED',
])
const terminalIngestFailure = computed(() =>
  terminalIngestErrorCodes.has(ingestStatus.value?.latest_job?.error_code || ''),
)
const ingestIsActive = computed(() =>
  ['queued', 'running', 'needs_cloud_confirmation'].includes(ingestJobStatus.value),
)
const ingestNotice = computed(() => {
  const status = ingestStatus.value
  if (!status) return pdfParsing.value ? 'PDF 正在解析中，论文全文内容将在稍后可用。你可先基于摘要提问。' : ''
  if (status.rag_ready && !ingestIsActive.value) return ''
  const job = status.latest_job
  if (job?.status === 'queued') return 'PDF 已保存，正在等待全文解析与索引。'
  if (job?.status === 'running') return `PDF 正在${job.current_step || '解析与索引'}，完成后会自动使用全文检索。`
  if (job?.status === 'needs_cloud_confirmation') return 'PDF 解析需要确认云端能力，当前可先基于摘要提问。'
  if (job?.status === 'failed') {
    if (job.error_code === 'PDF_ENCRYPTED') return '该 PDF 受密码保护，无法自动入库；请使用未加密版本后重新保存。'
    if (job.error_code === 'PDF_INVALID') return '该文件不是可解析的 PDF 或已损坏；请重新下载或上传有效 PDF。'
    if (job.error_code === 'PDF_FILE_MISSING') return '本地 PDF 文件已不存在；请回到文献库重新保存或下载。'
    if (job.error_code === 'QUALITY_GATE_FAILED') return 'PDF 已解析但内容质量不足，当前不能作为全文证据；可更换清晰版本后重新入库。'
    return 'PDF 全文入库失败；可重新入库，或继续基于摘要提问。'
  }
  if (job?.status === 'cancelled') return 'PDF 全文入库已取消；可重新入库。'
  return pdfParsing.value ? 'PDF 正在解析中，论文全文内容将在稍后可用。你可先基于摘要提问。' : ''
})
const canRetryIngest = computed(() =>
  hasLocalPdfForViewer.value
  && !ingestIsActive.value
  && !terminalIngestFailure.value
  && (!ingestStatus.value?.rag_ready || ['failed', 'cancelled'].includes(ingestJobStatus.value)),
)
const clearIngestPoll = () => {
  if (ingestPollTimer != null) {
    clearTimeout(ingestPollTimer)
    ingestPollTimer = null
  }
}
const contextModeLabel = (mode?: string): string => ({
  hybrid_rag_v2: '已使用论文索引检索',
  canonical_opening_v2: '已使用论文索引导读',
  canonical_degraded: '论文索引上下文已降级',
  legacy_fallback: '当前使用兼容阅读上下文',
  manual_memory_guidance: '记忆保存说明',
}[String(mode || '')] || '上下文状态未知')
const shouldShowContextStatus = (mode?: string, flags?: string[]): boolean =>
  Boolean(mode && (mode !== 'hybrid_rag_v2' || (flags?.length || 0) > 0))
const refreshIngestStatus = async (expectedPaperId = paperId.value) => {
  if (expectedPaperId == null) return
  try {
    const next = await getPaperIngestStatus(expectedPaperId)
    if (paperId.value !== expectedPaperId) return
    ingestStatus.value = next
    pdfParsing.value = ingestIsActive.value
    clearIngestPoll()
    if (ingestIsActive.value) {
      ingestPollTimer = setTimeout(() => { void refreshIngestStatus(expectedPaperId) }, 2500)
    }
  } catch {
    // The reader remains usable through its explicit fallback when the status
    // endpoint is temporarily unavailable.  Avoid repeatedly surfacing a
    // toast while a user is reading.
  }
}
const retryIngest = async () => {
  if (paperId.value == null) return
  ingestRetrying.value = true
  try {
    await enqueuePaperIngest(paperId.value)
    message.success('已创建全文入库任务')
    await refreshIngestStatus(paperId.value)
  } catch (e: unknown) {
    message.error((e as Error).message || '创建入库任务失败')
  } finally {
    ingestRetrying.value = false
  }
}
const releasePdfObjectUrl = () => {
  if (!pdfObjectUrl.value) return
  URL.revokeObjectURL(pdfObjectUrl.value)
  pdfObjectUrl.value = ''
}
const onPdfError = (msg: string) => {
  loadError.value = msg || 'PDF 加载失败'
  pdfReady.value = true
  void maybeStartOpening()
}
const pdfReady = ref(false)
const openingStarted = ref(false)
const openingLoading = ref(false)
const onPdfLoaded = () => {
  pdfReady.value = true
  void maybeStartOpening()
}
const leftPaneStyle = computed(() => {
  if (leftWidthPx.value == null) return {}
  return { flex: `0 0 ${leftWidthPx.value}px` }
})
const rightPaneStyle = computed(() => ({}))
const backToLibrary = () => {
  router.push('/library')
}
const retryLoadPaper = () => {
  loadError.value = ''
  pdfReady.value = false
  void loadPaper()
}
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))
const splitBounds = (width: number) => {
  const minLeft = Math.min(320, Math.max(200, width * 0.4))
  const minRight = Math.min(360, Math.max(240, width * 0.45))
  return {
    minLeft,
    maxLeft: Math.max(minLeft, width - minRight),
  }
}
const onChatWheel = (e: WheelEvent) => {
  e.stopPropagation()
}
const initDefaultSplitIfNeeded = () => {
  if (leftWidthPx.value != null) return
  const el = splitRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  if (!Number.isFinite(rect.width) || rect.width <= 0) return
  const desiredLeft = rect.width * 0.64
  const { minLeft, maxLeft } = splitBounds(rect.width)
  leftWidthPx.value = clamp(Math.round(desiredLeft), minLeft, maxLeft)
}
const setLeftWidthFromClientX = (clientX: number) => {
  const el = splitRef.value
  if (!el) return
  const rect = el.getBoundingClientRect()
  const { minLeft, maxLeft } = splitBounds(rect.width)
  const w = clamp(clientX - rect.left, minLeft, maxLeft)
  leftWidthPx.value = w
}
const scheduleDragUpdate = () => {
  if (rafPending.value) return
  rafPending.value = true
  requestAnimationFrame(() => {
    rafPending.value = false
    if (!dragging.value) return
    if (lastClientX.value == null) return
    setLeftWidthFromClientX(lastClientX.value)
  })
}
const onDividerPointerMove = (ev: PointerEvent) => {
  if (!dragging.value) return
  if (dragPointerId.value != null && ev.pointerId !== dragPointerId.value) return
  lastClientX.value = ev.clientX
  scheduleDragUpdate()
}
const endDrag = () => {
  if (!dragging.value) return
  dragging.value = false
  dragPointerId.value = null
  lastClientX.value = null
  rafPending.value = false
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  window.removeEventListener('pointermove', onDividerPointerMove)
  window.removeEventListener('pointerup', onDividerPointerUp)
  window.removeEventListener('pointercancel', onDividerPointerUp)
}
const onDividerPointerUp = (ev: PointerEvent) => {
  if (dragPointerId.value != null && ev.pointerId !== dragPointerId.value) return
  endDrag()
}
const onDividerPointerDown = (ev: PointerEvent) => {
  if (ev.button !== 0) return
  ev.preventDefault()
  dragging.value = true
  dragPointerId.value = ev.pointerId
  lastClientX.value = ev.clientX
  setLeftWidthFromClientX(ev.clientX)
  try {
    dividerRef.value?.setPointerCapture(ev.pointerId)
  } catch {
  }
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
  window.addEventListener('pointermove', onDividerPointerMove)
  window.addEventListener('pointerup', onDividerPointerUp)
  window.addEventListener('pointercancel', onDividerPointerUp)
}
onBeforeUnmount(() => {
  endDrag()
  clearIngestPoll()
  releasePdfObjectUrl()
  void flushReadingSession()
})
const flushReadingSession = async () => {
  const s = readingSession.value
  if (!s) return
  readingSession.value = null
  const durMs = Date.now() - s.startedAtMs
  const sec = Math.floor(durMs / 1000)
  if (!Number.isFinite(sec) || sec < 8) return
  try {
    await postReadingLog({ paper_id: s.paperId, duration_sec: Math.min(sec, 60 * 60 * 6), client_ts: Math.floor(Date.now() / 1000) })
  } catch {
  }
}
const scrollBottom = async () => {
  await nextTick()
  const el = scrollRef.value
  if (el) el.scrollTop = el.scrollHeight
}
const gotoCitationPage = (page?: number | null) => {
  if (!page || page < 1) return
  pdfViewerRef.value?.gotoPage(page)
}
const mapHistoryTurns = (
  turns: { role?: string; content?: string | null; created_at?: number }[]
): { role: 'user' | 'assistant'; content: string; related_papers?: Paper[] }[] =>
  turns
    .filter((t) => t && (t.role === 'user' || t.role === 'assistant') && String(t.content || '').trim())
    .map((t) => {
      const role = t.role as 'user' | 'assistant'
      const content = role === 'assistant' ? normalizeAssistantText(String(t.content)) : String(t.content)
      return { role, content }
    })
const ensureOpeningAndHistory = async (reloadHistory = false, showError = true) => {
  if (paperId.value == null) return
  try {
    const res = await postPaperReaderOpening(
      paperId.value,
      conversationId.value || undefined,
    )
    if (!res.success || !res.opening) return
    conversationId.value = res.conversation_id
    if (res.pdf_parsing) pdfParsing.value = true
    if (reloadHistory) {
      const h = await getPaperReaderHistory(paperId.value, 200)
      if (h?.success && Array.isArray(h.turns) && h.turns.length > 0) {
        conversationId.value = h.conversation_id
        const restored = mapHistoryTurns(h.turns)
        if (restored.length > 0) {
          messages.value = restored
          await scrollBottom()
          return
        }
      }
    }
    const hasAssistantMessage = messages.value.some((m) => m.role === 'assistant')
    if (!hasAssistantMessage) {
      messages.value.push({
        role: 'assistant',
        content: normalizeAssistantText(res.opening),
        context_mode: res.context_mode,
        degradation_flags: Array.isArray(res.degradation_flags) ? res.degradation_flags : undefined,
      })
      await scrollBottom()
    }
  } catch (e: unknown) {
    if (showError) {
      message.error((e as Error).message || '导读加载失败')
    }
  }
}
const maybeStartOpening = async (reloadHistory = false, showError = true) => {
  if (openingStarted.value) return
  if (paperId.value == null) return
  if (hasLocalPdfForViewer.value && !pdfReady.value) return
  openingStarted.value = true
  openingLoading.value = true
  try {
    await ensureOpeningAndHistory(reloadHistory, showError)
  } finally {
    openingLoading.value = false
  }
}
const send = async () => {
  const text = draft.value.trim()
  if (!text || paperId.value == null || sending.value || openingLoading.value) return
  if (composing.value) return
  sending.value = true
  messages.value.push({ role: 'user', content: text })
  draft.value = ''
  inputKey.value += 1
  await nextTick()
  await scrollBottom()
  try {
    const res = await postPaperReaderChat({
      paper_id: paperId.value,
      conversation_id: conversationId.value || undefined,
      user_message: text,
    })
    conversationId.value = res.conversation_id
    if (res.success && res.reply) {
      const rp = Array.isArray((res as any).related_papers) ? ((res as any).related_papers as Paper[]) : []
      const cites = Array.isArray((res as any).citations) ? ((res as any).citations as PaperReaderCitation[]) : []
      messages.value.push({
        role: 'assistant',
        content: normalizeAssistantText(res.reply),
        related_papers: rp.length ? rp : undefined,
        citations: cites.length ? cites : undefined,
        context_mode: res.context_mode,
        degradation_flags: Array.isArray(res.degradation_flags) ? res.degradation_flags : undefined,
      })
    } else {
      messages.value.push({ role: 'assistant', content: '（无回复）' })
    }
  } catch (e: unknown) {
    message.error((e as Error).message || '发送失败')
    messages.value.push({ role: 'assistant', content: '请求失败，请检查网络或 LLM 配置。' })
  } finally {
    sending.value = false
    await scrollBottom()
  }
}
const memoryKindLabel = (kind: CommitMemoryItem['kind']): string => ({
  reading_summary: '阅读总结',
  key_finding: '关键发现',
  open_question: '待解决问题',
  research_decision: '研究决策',
  preference: '用户偏好',
  research_goal: '研究目标',
}[kind])
const buildPaperMemoryContent = (
  payload: Awaited<ReturnType<typeof createMemoryDraft>>['draft']['payload'],
): string => {
  const sections: string[] = []
  if (payload.paper_summary.trim()) {
    sections.push(`阅读总结\n${payload.paper_summary.trim()}`)
  }
  const appendItems = (heading: string, items: { content: string }[]) => {
    const values = items.map((item) => item.content.trim()).filter(Boolean)
    if (values.length) sections.push(`${heading}\n${values.map((value) => `- ${value}`).join('\n')}`)
  }
  appendItems('关键发现', payload.key_findings)
  appendItems('待解决问题', payload.open_questions)
  appendItems('研究决策', payload.research_decisions)
  return sections.join('\n\n')
}
const resetMemoryDraftState = () => {
  memoryDraftId.value = ''
  paperMemoryItems.value = []
  userMemoryItems.value = []
}
const openMemoryDraft = async () => {
  if (paperId.value == null || !conversationId.value || memoryDraftLoading.value) {
    message.warning('当前阅读会话尚未建立，请先完成导读或一次问答')
    return
  }
  memoryDraftLoading.value = true
  try {
    const result = await createMemoryDraft(paperId.value, conversationId.value)
    const generated = result.draft
    const payload = generated.payload
    memoryDraftId.value = generated.id
    const paperMemoryContent = buildPaperMemoryContent(payload)
    paperMemoryItems.value = paperMemoryContent
      ? [{
          kind: 'reading_summary',
          content: paperMemoryContent,
          selected: true,
          confidence: 1,
        }]
      : []
    userMemoryItems.value = payload.user_memory_candidates.map((item) => ({
      kind: item.kind,
      content: item.content,
      selected: false,
      confidence: item.confidence,
    }))
    if (!paperMemoryItems.value.length && !userMemoryItems.value.length) {
      await cancelMemoryDraft(memoryDraftId.value)
      resetMemoryDraftState()
      message.info('本次对话没有生成值得保存的记忆')
      return
    }
    memoryDraftOpen.value = true
  } catch (e: unknown) {
    message.error((e as Error).message || 'Memory 草稿生成失败')
  } finally {
    memoryDraftLoading.value = false
  }
}
const commitSelectedMemory = async () => {
  const paperItems = paperMemoryItems.value
    .filter((item) => item.selected && item.content.trim())
    .map(({ kind, content }) => ({ kind, content: content.trim() }))
  const userItems = userMemoryItems.value
    .filter((item) => item.selected && item.content.trim())
    .map(({ kind, content }) => ({ kind, content: content.trim() }))
  if (!paperItems.length && !userItems.length) {
    message.warning('请至少选择一条记忆')
    return
  }
  memoryCommitLoading.value = true
  try {
    const idempotencyKey = typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`
    const result = await commitMemoryDraft(
      memoryDraftId.value,
      {
        paper_items: paperItems,
        accepted_user_items: userItems,
      },
      idempotencyKey,
    )
    memoryDraftOpen.value = false
    resetMemoryDraftState()
    message.success(`已写入 ${result.memories.length} 条确认记忆`)
  } catch (e: unknown) {
    message.error((e as Error).message || 'Memory 写入失败')
  } finally {
    memoryCommitLoading.value = false
  }
}
const discardMemoryDraft = async () => {
  if (memoryCommitLoading.value || memoryCancelLoading.value) return
  const draftId = memoryDraftId.value
  if (!draftId) {
    memoryDraftOpen.value = false
    resetMemoryDraftState()
    return
  }
  memoryCancelLoading.value = true
  try {
    await cancelMemoryDraft(draftId)
    memoryDraftOpen.value = false
    resetMemoryDraftState()
    message.success('已放弃本次 Memory 草稿')
  } catch (e: unknown) {
    memoryDraftOpen.value = true
    message.error((e as Error).message || 'Memory 草稿取消失败')
  } finally {
    memoryCancelLoading.value = false
  }
}
const paperExternalUrl = (p: Paper): string | null => {
  if (!p) return null
  const src = String(p.source_url || '').trim()
  if (src && /^https?:\/\//i.test(src)) return src
  const pdf = String(p.pdf_url || '').trim()
  if (pdf && /^https?:\/\//i.test(pdf)) return pdf
  let ax = String(p.arxiv_id || '').trim()
  if (ax) {
    ax = ax.replace(/^arxiv:/i, '').replace(/\.pdf$/i, '')
    return `https://arxiv.org/abs/${ax}`
  }
  const doiRaw = String(p.doi || '').trim()
  if (doiRaw) {
    if (/^https?:\/\//i.test(doiRaw)) return doiRaw
    return `https://doi.org/${doiRaw.replace(/^doi:/i, '')}`
  }
  return null
}
const relatedPaperMetaLine = (p: Paper): string => {
  const parts: string[] = []
  const names = (p.authors || []).map((a: { name?: string }) => a?.name).filter(Boolean) as string[]
  if (names.length) parts.push(names.slice(0, 4).join(', ') + (names.length > 4 ? '…' : ''))
  if (p.year != null) parts.push(String(p.year))
  const j = String((p as { journal?: string }).journal || (p as { venue?: string }).venue || '').trim()
  if (j) parts.push(j)
  let s = parts.join(' · ')
  if (s.length > 140) s = `${s.slice(0, 137)}…`
  return s
}
const saveRelatedPaperToLibrary = async (p: Paper) => {
  if (!p) return
  try {
    await savePapers([p], { llm_classify: false })
    message.success('已保存到文献库')
  } catch (e: unknown) {
    message.error((e as Error).message || '保存失败')
  }
}
function escapeBib(s: string): string {
  return String(s || '').replace(/([&%$#_{}~^\\])/g, '\\$1')
}
function generateBibTeX(p: Paper): string {
  const authors = (p.authors || []).map((a) => a.name).filter(Boolean).join(' and ')
  const year = p.year ?? ''
  const key = `${(p.authors?.[0]?.name || 'unknown').split(' ').pop()?.toLowerCase() || 'unknown'}${year}`
  const lines = [`@article{${key},`]
  if (authors) lines.push(`  author = {${escapeBib(authors)}},`)
  if (p.title) lines.push(`  title = {${escapeBib(p.title)}},`)
  if (p.journal) lines.push(`  journal = {${escapeBib(String(p.journal))}},`)
  if (year) lines.push(`  year = {${year}},`)
  if (p.doi) lines.push(`  doi = {${p.doi}},`)
  if (p.arxiv_id) lines.push(`  eprint = {${p.arxiv_id}},`)
  if (p.source_url) lines.push(`  url = {${p.source_url}},`)
  lines.push('}')
  return lines.join('\n')
}
function generateAPA(p: Paper): string {
  const authors = (p.authors || []).map((a) => a.name).filter(Boolean)
  const authorStr = authors.length > 0
    ? authors.length <= 3
      ? authors.join(', ') + (authors.length === 2 ? ' & ' : authors.length === 1 ? '' : ', & ')
      : authors[0] + ', et al.'
    : ''
  const year = p.year ? `(${p.year})` : ''
  const title = p.title || ''
  const venue = p.journal || ''
  const doi = p.doi ? ` https://doi.org/${p.doi}` : ''
  return [authorStr, year, title, venue, doi].filter(Boolean).join('. ') + '.'
}
const onCopyCitation = async ({ key }: { key: string }) => {
  if (!paper.value) return
  let text = ''
  if (key === 'bibtex') text = generateBibTeX(paper.value)
  else if (key === 'apa') text = generateAPA(paper.value)
  else text = `${paper.value.title || ''}\n${(paper.value.authors || []).map((a) => a.name).join(', ')}\n${paper.value.journal || ''} ${paper.value.year ?? ''}\n${paper.value.doi ? 'DOI: ' + paper.value.doi : ''}`
  try {
    await navigator.clipboard.writeText(text)
    message.success(`已复制${key === 'bibtex' ? ' BibTeX' : key === 'apa' ? ' APA' : ''}引用`)
  } catch {
    message.error('复制失败，请手动选择文本复制')
  }
}
const loadPaper = async () => {
  await flushReadingSession()
  loadingPaper.value = true
  loadError.value = ''
  paper.value = null
  ingestStatus.value = null
  clearIngestPoll()
  conversationId.value = ''
  releasePdfObjectUrl()
  pdfReady.value = false
  openingStarted.value = false
  openingLoading.value = false
  messages.value = []
  await nextTick()
  initDefaultSplitIfNeeded()
  if (paperId.value == null) {
    loadError.value = '无效的文献 ID'
    loadingPaper.value = false
    return
  }
  readingSession.value = { paperId: paperId.value, startedAtMs: Date.now() }
  try {
    paper.value = await getPaper(paperId.value)
    void refreshIngestStatus(paperId.value)
    if (hasLocalPdfForViewer.value) {
      try {
        pdfObjectUrl.value = URL.createObjectURL(
          await getLibraryPdfBlob(paperId.value),
        )
      } catch (e: unknown) {
        pdfReady.value = true
        message.warning((e as Error).message || 'PDF 加载失败，可继续基于摘要问答')
      }
    }
    try {
      const h = await getPaperReaderHistory(paperId.value, 200)
      if (h?.success) conversationId.value = h.conversation_id
      if (h?.success && Array.isArray(h.turns) && h.turns.length > 0) {
        const restored = mapHistoryTurns(h.turns)
        if (restored.length > 0) {
          messages.value = restored
          await scrollBottom()
        }
      }
    } catch {
    }
    if (messages.value.length === 0) {
      void maybeStartOpening()
    } else if (messages.value[0]?.role === 'user') {
      void maybeStartOpening(true, false)
    }
  } catch (e: unknown) {
    loadError.value = (e as Error).message || '加载失败'
  } finally {
    loadingPaper.value = false
  }
}
watch(
  () => route.params.id,
  () => {
    void loadPaper()
  },
  { immediate: true }
)
onMounted(() => {
  initDefaultSplitIfNeeded()
})
</script>
<style scoped>
.paper-reader {
  display: flex;
  flex-direction: column;
  height: 100vh;
  min-height: 0;
  overflow: hidden;
  color-scheme: light;
}
.paper-reader__toolbar {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  padding: 14px 20px;
  border-bottom: 1px solid var(--pg-divider);
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: var(--pg-glass-blur-light);
  -webkit-backdrop-filter: var(--pg-glass-blur-light);
}
.paper-reader__title--placeholder {
  color: var(--pg-text-tertiary);
  font-weight: 500;
}
.paper-reader__title {
  flex: 1;
  min-width: 120px;
  font-family: var(--pg-font-serif);
  font-weight: 600;
  font-size: 16px;
  color: var(--pg-text-heading);
  line-height: 1.4;
  letter-spacing: 0.005em;
}
.paper-reader__toolbar-actions {
  flex-shrink: 0;
}
.paper-reader__err {
  flex: 0 0 auto;
  color: #cf1322;
  padding: 16px;
}
.paper-reader__notice {
  flex: 0 0 auto;
  padding: 9px 20px;
  color: var(--pg-text-secondary);
  background: var(--pg-bg-soft);
  border-bottom: 1px solid var(--pg-divider);
  font-size: 13px;
  line-height: 1.5;
}
.paper-reader__ingest-notice {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.paper-reader__pdf-placeholder {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 24px;
  text-align: center;
}
.paper-reader__split {
  display: flex;
  flex-direction: row;
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
}
.paper-reader__pane {
  flex: 1;
  min-width: clamp(200px, 30vw, 280px);
  min-height: 0;
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--pg-surface);
}
.paper-reader__pane--pdf {
  min-height: 0;
  border-right: 1px solid var(--pg-divider);
}
.paper-reader__divider {
  flex: 0 0 6px;
  cursor: col-resize;
  background: transparent;
  position: relative;
  touch-action: none;
}
.paper-reader__divider::before {
  content: '';
  position: absolute;
  left: 2px;
  top: 0;
  bottom: 0;
  width: 2px;
  background: var(--pg-divider);
  transition: background 0.15s ease;
}
.paper-reader__divider:hover::before {
  background: var(--pg-primary);
  opacity: 0.4;
}
.pdf-js-viewer-wrapper {
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.paper-reader__pane--chat {
  min-height: 0;
  height: 100%;
  position: relative;
  display: flex;
  flex-direction: column;
  background: var(--pg-bg-soft);
  overflow: hidden;
}
.paper-reader__messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 18px;
  background: transparent;
  margin: 12px;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
  scrollbar-width: thin;
  scrollbar-color: rgba(91,100,114,0.25) transparent;
}
.paper-reader__messages::-webkit-scrollbar {
  width: 8px;
}
.paper-reader__messages::-webkit-scrollbar-track {
  background: transparent;
}
.paper-reader__messages::-webkit-scrollbar-thumb {
  background: rgba(91,100,114,0.22);
  border-radius: 999px;
}
.paper-reader__messages::-webkit-scrollbar-thumb:hover {
  background: rgba(91,100,114,0.4);
}
.paper-reader__messages::-webkit-scrollbar-button {
  display: none;
}
.paper-reader__msg {
  margin-bottom: 18px;
  display: flex;
  gap: 10px;
  align-items: flex-start;
}
.paper-reader__msg--user {
  justify-content: flex-end;
}
.paper-reader__avatar {
  width: 30px;
  height: 30px;
  flex-shrink: 0;
  border-radius: 9px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  margin-top: 2px;
}
.paper-reader__avatar--assistant {
  background: var(--pg-surface);
  border: 1px solid var(--pg-border);
  color: var(--pg-primary);
  box-shadow: var(--pg-shadow-xs);
}
.paper-reader__bubble {
  max-width: 82%;
  padding: 12px 16px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.65;
  min-width: 0;
}
.paper-reader__bubble--user {
  background: var(--pg-primary);
  color: var(--pg-text-inverse);
  border-radius: 14px 14px 4px 14px;
  box-shadow: 0 4px 14px rgba(30, 27, 75, 0.18);
}
.paper-reader__bubble--assistant {
  background: var(--pg-surface);
  border: 1px solid var(--pg-border);
  border-radius: 4px 14px 14px 14px;
  box-shadow: var(--pg-shadow-sm);
  color: var(--pg-text);
}
.paper-reader__msg-role {
  font-size: 12px;
  color: var(--pg-text-tertiary);
  margin-bottom: 4px;
  font-weight: 500;
}
.paper-reader__msg-body {
  line-height: 1.6;
  font-size: 14px;
  color: var(--pg-text);
}
.paper-reader__bubble--user .paper-reader__msg-body {
  color: var(--pg-text-inverse);
  white-space: pre-wrap;
}
.paper-reader__msg-body :deep(h1),
.paper-reader__msg-body :deep(h2),
.paper-reader__msg-body :deep(h3) {
  margin: 10px 0 6px;
  line-height: 1.25;
}
.paper-reader__msg-body :deep(h1) {
  font-size: 18px;
}
.paper-reader__msg-body :deep(h2) {
  font-size: 16px;
}
.paper-reader__msg-body :deep(h4),
.paper-reader__msg-body :deep(h5),
.paper-reader__msg-body :deep(h6) {
  margin: 8px 0 4px;
  line-height: 1.3;
  font-size: 14px;
  font-weight: 600;
  color: var(--pg-text-heading);
}
.paper-reader__msg-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--pg-divider);
  margin: 12px 0;
}
.paper-reader__msg-body :deep(table) {
  width: 100%;
  border-collapse: collapse;
  margin: 10px 0;
  font-size: 13px;
  line-height: 1.45;
  display: block;
  overflow-x: auto;
  max-width: 100%;
  -webkit-overflow-scrolling: touch;
}
.paper-reader__msg-body :deep(thead th) {
  background: var(--pg-bg-soft);
  font-weight: 600;
  text-align: left;
  white-space: nowrap;
}
.paper-reader__msg-body :deep(th),
.paper-reader__msg-body :deep(td) {
  border: 1px solid var(--pg-border);
  padding: 6px 8px;
  vertical-align: top;
  word-break: break-word;
}
.paper-reader__msg-body :deep(tbody tr:nth-child(even)) {
  background: var(--pg-bg-soft);
}
.paper-reader__msg-body :deep(ul),
.paper-reader__msg-body :deep(ol) {
  margin: 4px 0 4px 18px;
  padding: 0;
}
.paper-reader__msg-body :deep(li) {
  margin: 2px 0;
}
.paper-reader__msg-body :deep(code) {
  background: var(--pg-bg-soft);
  border-radius: 4px;
  padding: 1px 4px;
  font-size: 12px;
}
.paper-reader__msg-body :deep(pre) {
  background: var(--pg-bg-soft);
  border-radius: 8px;
  padding: 10px;
  overflow: auto;
  margin: 8px 0;
}
.paper-reader__msg-body :deep(pre code) {
  background: transparent;
  padding: 0;
}
.paper-reader__related {
  border-top: 1px solid var(--pg-divider);
  margin-top: 10px;
  padding-top: 10px;
}
.paper-reader__citations {
  margin-top: 8px;
}
.paper-reader__citations-title {
  font-size: 12px;
  color: var(--pg-text-tertiary);
  margin-bottom: 6px;
  display: block;
}
.paper-reader__citations-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.paper-reader__context-status {
  margin-top: 8px;
  color: var(--pg-text-tertiary);
  font-size: 12px;
  line-height: 1.5;
}
.paper-reader__citation-chip {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 600;
  line-height: 1.5;
  color: var(--pg-primary-hover, #4f46e5);
  background: var(--pg-primary-soft, #eef0ff);
  border: 1px solid transparent;
  border-radius: var(--pg-radius-pill, 999px);
  cursor: pointer;
  transition: background 0.15s ease, color 0.15s ease;
}
.paper-reader__citation-chip:hover {
  background: var(--pg-primary, #6366f1);
  color: var(--pg-text-inverse);
}
.paper-reader__citation-chip--static,
.paper-reader__citation-chip:disabled {
  cursor: default;
  opacity: 0.7;
}
.paper-reader__citation-chip:disabled:hover {
  background: var(--pg-primary-soft, #eef0ff);
  color: var(--pg-primary-hover, #4f46e5);
}
.paper-reader__related-title {
  font-size: 12px;
  color: var(--pg-text-tertiary);
  margin-bottom: 8px;
}
.paper-reader__related-cards {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.paper-reader__related-card {
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius);
  padding: 10px 12px;
  background: var(--pg-bg-soft);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.paper-reader__related-card:hover {
  border-color: #d9ddf5;
  box-shadow: var(--pg-shadow-sm);
}
.paper-reader__related-card-head {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  line-height: 1.4;
}
.paper-reader__related-idx {
  flex-shrink: 0;
  color: var(--pg-primary);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
}
.paper-reader__related-title-link {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--pg-text);
  text-decoration: none;
  word-break: break-word;
}
.paper-reader__related-title-link:hover {
  color: var(--pg-primary-hover);
}
.paper-reader__related-title-link--text {
  color: var(--pg-text);
  cursor: default;
}
.paper-reader__related-card-meta {
  color: var(--pg-text-tertiary);
  font-size: 12px;
  line-height: 1.35;
  margin-top: 4px;
  padding-left: 1.5em;
}
.paper-reader__related-card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 12px;
  align-items: center;
  margin-top: 8px;
  padding-left: 1.5em;
}
.paper-reader__related-act.ant-btn-sm {
  height: auto;
  line-height: 1.35;
}
.paper-reader__input {
  display: flex;
  gap: 8px;
  padding: 12px 14px;
  background: var(--pg-surface);
  border-top: 1px solid var(--pg-divider);
  align-items: center;
}
.paper-reader__memory-section {
  margin-top: 18px;
}
.paper-reader__memory-section:first-child {
  margin-top: 0;
}
.paper-reader__memory-modal-body {
  max-height: min(62vh, 620px);
  overflow-y: auto;
  padding-right: 6px;
}
.paper-reader__memory-section h4 {
  margin: 0 0 10px;
}
.paper-reader__memory-item {
  display: grid;
  gap: 7px;
  margin-bottom: 14px;
}
.paper-reader__input :deep(.ant-input) {
  flex: 1;
  border-radius: var(--pg-radius);
  transition: border-color 0.18s ease, box-shadow 0.18s ease;
}
.paper-reader__input :deep(.ant-input:focus),
.paper-reader__input :deep(textarea.ant-input:focus) {
  border-color: var(--pg-primary);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.1);
}
.paper-reader__input :deep(textarea.ant-input) {
  min-height: 36px;
  height: 36px;
  line-height: 22px;
  resize: none;
  padding: 6px 12px;
}
@media (max-width: 900px) {
  .paper-reader__split {
    flex-direction: column;
  }
  .paper-reader__divider {
    display: none;
  }
  .paper-reader__pane {
    min-width: 0;
    min-height: 240px;
  }
  .paper-reader__pane--pdf {
    border-right: 0;
    border-bottom: 1px solid var(--pg-divider);
  }
}
</style>
