<template>
  <section class="research-page">
    <div v-if="!session" class="research-start">
      <div class="research-start__intro">
        <h1>协同研究</h1>
        <p>从个人文献库选择 1—8 篇论文，在同一对话中进行比较、归纳和综述构思。</p>
      </div>
      <a-alert
        type="info"
        show-icon
        message="优先检索已完成全文入库的论文；尚未入库的论文只提供摘要背景，回答会明确标注证据范围。"
      />
      <a-card title="选择研究材料" :bordered="false">
        <a-select
          v-model:value="selectedPaperIds"
          mode="multiple"
          show-search
          :filter-option="filterPaper"
          :loading="loadingPapers"
          :max-tag-count="3"
          placeholder="选择个人文献库中的论文"
          class="research-start__select"
        >
          <a-select-option
            v-for="paper in libraryPapers"
            :key="paper.id"
            :value="paper.id"
            :label="paper.title"
          >
            <div class="research-option">
              <span>{{ paper.title }}</span>
              <small>{{ paper.year || '年份未知' }} · {{ paper.category || '未分类' }}</small>
            </div>
          </a-select-option>
        </a-select>
        <div class="research-start__actions">
          <span>已选择 {{ selectedPaperIds.length }} 篇</span>
          <a-button
            type="primary"
            :loading="creating"
            :disabled="selectedPaperIds.length < 1 || selectedPaperIds.length > 8"
            @click="startResearch"
          >
            开始研究
          </a-button>
        </div>
      </a-card>
    </div>

    <div v-else class="research-workspace">
      <aside
        class="research-sources"
        :class="{ 'research-sources--collapsed': sourcesCollapsed }"
      >
        <div class="research-sources__head">
          <strong v-if="!sourcesCollapsed">研究材料</strong>
          <a-button
            type="text"
            :aria-label="sourcesCollapsed ? '展开研究材料' : '收起研究材料'"
            @click="sourcesCollapsed = !sourcesCollapsed"
          >
            <template #icon>
              <MenuUnfoldOutlined v-if="sourcesCollapsed" />
              <MenuFoldOutlined v-else />
            </template>
          </a-button>
        </div>
        <div v-if="!sourcesCollapsed" class="research-sources__body">
          <article v-for="(paper, index) in session.papers" :key="paper.id">
            <span class="research-sources__index">{{ index + 1 }}</span>
            <div>
              <h3>{{ paper.title }}</h3>
              <p>{{ paper.year || '年份未知' }} · {{ paper.category || '未分类' }}</p>
            </div>
          </article>
          <a-button block @click="newResearch">重新选择论文</a-button>
        </div>
      </aside>

      <main class="research-chat">
        <header class="research-chat__header">
          <div>
            <h2>{{ session.title }}</h2>
            <span>{{ session.papers.length }} 篇论文 · {{ researchContextLabel }}</span>
          </div>
        </header>
        <div ref="messagesEl" class="research-chat__messages">
          <div v-if="!session.turns.length" class="research-chat__welcome">
            <ExperimentOutlined />
            <h3>可以开始协同分析了</h3>
            <p>例如：比较这些论文的方法差异，或为它们生成一份文献综述提纲。</p>
          </div>
          <div
            v-for="turn in session.turns"
            :key="turn.id"
            class="research-message"
            :class="`research-message--${turn.role}`"
          >
            <div class="research-message__content">
              <div class="research-message__bubble">{{ turn.content }}</div>
              <div
                v-if="turn.role === 'assistant' && citationsForTurn(turn).length"
                class="research-message__citations"
                aria-label="本回答的 PDF 证据"
              >
                <a-tooltip
                  v-for="citation in citationsForTurn(turn)"
                  :key="`${turn.id}-${citation.evidence_id}`"
                  :title="citation.snippet"
                >
                  <a-tag class="research-message__citation" color="blue">
                    {{ citation.marker }} · {{ citation.paper_title || `论文 ${citation.paper_id}` }}
                    <template v-if="citation.page_start">
                      · p{{ citation.page_start }}<span v-if="citation.page_end && citation.page_end !== citation.page_start">–{{ citation.page_end }}</span>
                    </template>
                  </a-tag>
                </a-tooltip>
              </div>
            </div>
          </div>
          <div v-if="sending" class="research-message research-message--assistant">
            <div class="research-message__bubble research-message__loading">
              <a-spin size="small" /> 正在综合所选论文…
            </div>
          </div>
        </div>
        <div class="research-chat__composer">
          <a-textarea
            v-model:value="input"
            :auto-size="{ minRows: 1, maxRows: 5 }"
            :maxlength="4000"
            placeholder="询问多篇论文的共同点、差异、研究空白或综述结构…"
            @keydown.enter.exact.prevent="sendMessage"
          />
          <a-button type="primary" :loading="sending" @click="sendMessage">
            发送
          </a-button>
        </div>
      </main>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import {
  ExperimentOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from '@ant-design/icons-vue'
import { getLibrary } from '@/services/api'
import {
  createResearchSession,
  sendResearchMessage,
  type ResearchCitation,
  type ResearchSession,
  type ResearchTurn,
} from '@/services/api'
import type { Paper } from '@/types'

const libraryPapers = ref<Paper[]>([])
const selectedPaperIds = ref<number[]>([])
const session = ref<ResearchSession | null>(null)
const loadingPapers = ref(false)
const creating = ref(false)
const sending = ref(false)
const sourcesCollapsed = ref(false)
const input = ref('')
const messagesEl = ref<HTMLElement | null>(null)

const latestAssistantMetadata = computed(() => {
  const turns = session.value?.turns ?? []
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    if (turns[index].role === 'assistant') return turns[index].metadata
  }
  return null
})

const researchContextLabel = computed(() => {
  const mode = latestAssistantMetadata.value?.context_mode
  if (mode === 'multi_paper_hybrid_rag_v1') return '全文证据检索模式'
  if (mode === 'multi_paper_hybrid_rag_partial_v1') return '全文证据 + 摘要补充模式'
  if (mode === 'multi_paper_canonical_no_hit_v1' || mode === 'multi_paper_canonical_partial_no_hit_v1') {
    return '全文索引未召回相关片段'
  }
  if (mode === 'multi_paper_canonical_no_evidence_v1') return '全文证据受上下文预算限制'
  if (mode === 'metadata_abstract_degraded_v1') return '摘要降级模式'
  return '摘要背景模式'
})

const citationsForTurn = (turn: ResearchTurn): ResearchCitation[] => (
  turn.role === 'assistant' ? turn.metadata.citations ?? [] : []
)

const loadPapers = async () => {
  loadingPapers.value = true
  try {
    const result = await getLibrary(500)
    libraryPapers.value = (result.papers ?? []).filter(
      (paper): paper is Paper & { id: number } => typeof paper.id === 'number',
    )
  } catch (error: unknown) {
    message.error((error as Error).message || '个人文献库加载失败')
  } finally {
    loadingPapers.value = false
  }
}

const filterPaper = (inputValue: string, option: { label?: string }) => (
  String(option?.label || '').toLowerCase().includes(inputValue.toLowerCase())
)

const scrollBottom = async () => {
  await nextTick()
  if (messagesEl.value) {
    messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  }
}

const startResearch = async () => {
  if (!selectedPaperIds.value.length) {
    message.warning('请至少选择一篇论文')
    return
  }
  creating.value = true
  try {
    session.value = await createResearchSession({
      paper_ids: selectedPaperIds.value,
    })
    await scrollBottom()
  } catch (error: unknown) {
    message.error((error as Error).message || '协同研究会话创建失败')
  } finally {
    creating.value = false
  }
}

const sendMessage = async () => {
  const content = input.value.trim()
  if (!content || !session.value || sending.value) return
  const activeSession = session.value
  const optimisticId = -Date.now()
  activeSession.turns.push({
    id: optimisticId,
    role: 'user',
    content,
    metadata: {},
    created_at: Math.floor(Date.now() / 1000),
  })
  input.value = ''
  sending.value = true
  await scrollBottom()
  try {
    const result = await sendResearchMessage(activeSession.id, content)
    activeSession.turns = [
      ...activeSession.turns.filter((turn) => turn.id !== optimisticId),
      ...result.turns,
    ]
    await scrollBottom()
  } catch (error: unknown) {
    activeSession.turns = activeSession.turns.filter((turn) => turn.id !== optimisticId)
    input.value = content
    message.error((error as Error).message || '协同研究回答失败')
  } finally {
    sending.value = false
  }
}

const newResearch = () => {
  session.value = null
  input.value = ''
  sourcesCollapsed.value = false
}

onMounted(loadPapers)
</script>

<style scoped>
.research-page {
  flex: 1;
  min-height: 0;
  height: 100%;
}
.research-start {
  width: min(980px, calc(100% - 40px));
  margin: 28px auto;
  display: grid;
  gap: 18px;
}
.research-start__intro h1 {
  margin: 0;
  color: var(--pg-text-heading);
  font-family: var(--pg-font-serif);
  font-size: 28px;
}
.research-start__intro p {
  margin: 8px 0 0;
  color: var(--pg-text-secondary);
}
.research-start__select {
  width: 100%;
}
.research-option {
  display: grid;
  line-height: 1.4;
}
.research-option small {
  color: var(--pg-text-tertiary);
}
.research-start__actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 18px;
  color: var(--pg-text-secondary);
}
.research-workspace {
  height: 100%;
  min-height: 0;
  display: flex;
  overflow: hidden;
  background: var(--pg-surface);
}
.research-sources {
  flex: 0 0 300px;
  min-width: 0;
  display: flex;
  flex-direction: column;
  border-right: 1px solid var(--pg-divider);
  background: var(--pg-bg-soft);
  transition: flex-basis 0.2s ease;
}
.research-sources--collapsed {
  flex-basis: 56px;
}
.research-sources__head {
  min-height: 58px;
  padding: 10px 12px 10px 18px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid var(--pg-divider);
}
.research-sources__body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 14px;
}
.research-sources article {
  display: flex;
  gap: 10px;
  padding: 12px 0;
  border-bottom: 1px solid var(--pg-divider);
}
.research-sources article:last-of-type {
  margin-bottom: 16px;
}
.research-sources__index {
  flex: 0 0 24px;
  height: 24px;
  display: grid;
  place-items: center;
  border-radius: 7px;
  background: var(--pg-primary-soft);
  color: var(--pg-primary);
  font-size: 12px;
  font-weight: 700;
}
.research-sources h3 {
  margin: 0;
  color: var(--pg-text-heading);
  font-size: 13px;
  line-height: 1.45;
}
.research-sources p {
  margin: 5px 0 0;
  color: var(--pg-text-tertiary);
  font-size: 12px;
}
.research-chat {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.research-chat__header {
  flex: 0 0 auto;
  min-height: 58px;
  display: flex;
  align-items: center;
  padding: 9px 20px;
  border-bottom: 1px solid var(--pg-divider);
}
.research-chat__header h2 {
  margin: 0;
  color: var(--pg-text-heading);
  font-size: 16px;
}
.research-chat__header span {
  color: var(--pg-text-tertiary);
  font-size: 12px;
}
.research-chat__messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 24px clamp(18px, 5vw, 70px);
  background: var(--pg-bg-soft);
  overscroll-behavior: contain;
}
.research-chat__welcome {
  max-width: 520px;
  margin: 12vh auto 0;
  text-align: center;
  color: var(--pg-text-secondary);
}
.research-chat__welcome > .anticon {
  font-size: 34px;
  color: var(--pg-primary);
}
.research-chat__welcome h3 {
  margin: 13px 0 6px;
  color: var(--pg-text-heading);
}
.research-chat__welcome p {
  margin: 0;
}
.research-message {
  display: flex;
  margin-bottom: 16px;
}
.research-message--user {
  justify-content: flex-end;
}
.research-message__content {
  max-width: min(780px, 86%);
  min-width: 0;
}
.research-message__bubble {
  max-width: 100%;
  padding: 12px 15px;
  border: 1px solid var(--pg-border);
  border-radius: 4px 14px 14px 14px;
  background: var(--pg-surface);
  color: var(--pg-text);
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  box-shadow: var(--pg-shadow-xs);
}
.research-message--user .research-message__bubble {
  border: none;
  border-radius: 14px 14px 4px 14px;
  background: var(--pg-primary);
  color: var(--pg-text-inverse);
}
.research-message__citations {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 7px;
}
.research-message__citation {
  max-width: 100%;
  margin-inline-end: 0;
  overflow: hidden;
  color: var(--pg-primary);
  cursor: help;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.research-message__loading {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--pg-text-secondary);
}
.research-chat__composer {
  flex: 0 0 auto;
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 14px 18px;
  border-top: 1px solid var(--pg-divider);
  background: var(--pg-surface);
}
.research-chat__composer :deep(.ant-input) {
  resize: none;
}
@media (max-width: 760px) {
  .research-sources {
    flex-basis: 220px;
  }
  .research-sources--collapsed {
    flex-basis: 50px;
  }
  .research-chat__messages {
    padding: 18px 12px;
  }
}
</style>
