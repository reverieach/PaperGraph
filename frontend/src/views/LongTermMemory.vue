<template>
  <section class="memory-page">
    <div class="memory-page__header">
      <div>
        <h1>长期记忆</h1>
        <p>仅保存你主动确认的研究偏好和长期研究目标。</p>
      </div>
      <a-button type="primary" @click="openCreate">
        <template #icon><PlusOutlined /></template>
        添加记忆
      </a-button>
    </div>

    <a-alert
      type="info"
      show-icon
      message="这些记忆属于当前账号，阅读助手会在相关问题中按需参考。"
    />

    <a-spin :spinning="loading">
      <div v-if="items.length" class="memory-list">
        <article v-for="item in items" :key="item.id" class="memory-card">
          <div class="memory-card__head">
            <a-tag :color="item.kind === 'research_goal' ? 'purple' : 'blue'">
              {{ kindLabel(item.kind) }}
            </a-tag>
            <a-button
              type="text"
              danger
              size="small"
              aria-label="删除记忆"
              @click="confirmDelete(item)"
            >
              <template #icon><DeleteOutlined /></template>
            </a-button>
          </div>
          <div class="memory-card__content">{{ item.content }}</div>
          <div class="memory-card__meta">
            {{ sourceLabel(item.source_type) }} · {{ formatTime(item.updated_at) }}
          </div>
        </article>
      </div>
      <a-empty
        v-else-if="!loading"
        description="还没有长期记忆，可以手动添加第一条。"
      />
    </a-spin>

    <a-modal
      v-model:open="createOpen"
      title="添加长期记忆"
      :confirm-loading="saving"
      ok-text="保存"
      cancel-text="取消"
      @ok="saveMemory"
    >
      <div class="memory-form">
        <label>
          <span>记忆类型</span>
          <a-select v-model:value="form.kind">
            <a-select-option value="preference">研究偏好</a-select-option>
            <a-select-option value="research_goal">长期研究目标</a-select-option>
          </a-select>
        </label>
        <label>
          <span>记忆内容</span>
          <a-textarea
            v-model:value="form.content"
            :maxlength="4000"
            :auto-size="{ minRows: 5, maxRows: 10 }"
            show-count
            placeholder="例如：我更关注可解释的混合检索方案，并希望优先比较公开数据集上的结果。"
          />
        </label>
      </div>
    </a-modal>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons-vue'
import {
  createUserMemory,
  deleteMemory,
  getUserMemories,
  type MemoryItem,
  type UserMemoryKind,
} from '@/services/api'

const loading = ref(false)
const saving = ref(false)
const createOpen = ref(false)
const items = ref<MemoryItem[]>([])
const form = reactive<{ kind: UserMemoryKind; content: string }>({
  kind: 'preference',
  content: '',
})

const kindLabel = (kind: string) => (
  kind === 'research_goal' ? '长期研究目标' : '研究偏好'
)
const sourceLabel = (source: string) => (
  source === 'manual' ? '手动添加' : '阅读总结确认'
)
const formatTime = (timestamp: number) => new Date(timestamp * 1000).toLocaleString('zh-CN')

const loadMemories = async () => {
  loading.value = true
  try {
    items.value = await getUserMemories()
  } catch (error: unknown) {
    message.error((error as Error).message || '长期记忆加载失败')
  } finally {
    loading.value = false
  }
}

const openCreate = () => {
  form.kind = 'preference'
  form.content = ''
  createOpen.value = true
}

const saveMemory = async () => {
  const content = form.content.trim()
  if (!content) {
    message.warning('请输入记忆内容')
    return
  }
  saving.value = true
  try {
    const result = await createUserMemory({ kind: form.kind, content })
    createOpen.value = false
    await loadMemories()
    message.success(result.created ? '长期记忆已保存' : '相同记忆已经存在')
  } catch (error: unknown) {
    message.error((error as Error).message || '长期记忆保存失败')
  } finally {
    saving.value = false
  }
}

const confirmDelete = (item: MemoryItem) => {
  Modal.confirm({
    title: '删除这条长期记忆？',
    content: '删除后，后续对话将不再引用它。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        await deleteMemory(item.id)
        items.value = items.value.filter((candidate) => candidate.id !== item.id)
        message.success('记忆已删除')
      } catch (error: unknown) {
        message.error((error as Error).message || '删除失败')
        throw error
      }
    },
  })
}

onMounted(loadMemories)
</script>

<style scoped>
.memory-page {
  width: min(980px, 100%);
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.memory-page__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}
.memory-page__header h1 {
  margin: 0;
  color: var(--pg-text-heading);
  font-family: var(--pg-font-serif);
  font-size: 26px;
}
.memory-page__header p {
  margin: 7px 0 0;
  color: var(--pg-text-secondary);
}
.memory-list {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(290px, 1fr));
  gap: 14px;
}
.memory-card {
  min-width: 0;
  padding: 16px 18px;
  border: 1px solid var(--pg-border);
  border-radius: var(--pg-radius-lg);
  background: var(--pg-surface);
  box-shadow: var(--pg-shadow-xs);
}
.memory-card__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.memory-card__content {
  color: var(--pg-text);
  line-height: 1.7;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.memory-card__meta {
  margin-top: 13px;
  color: var(--pg-text-tertiary);
  font-size: 12px;
}
.memory-form {
  display: grid;
  gap: 18px;
}
.memory-form label {
  display: grid;
  gap: 8px;
}
.memory-form label > span {
  color: var(--pg-text-heading);
  font-weight: 600;
}
@media (max-width: 640px) {
  .memory-page__header {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
