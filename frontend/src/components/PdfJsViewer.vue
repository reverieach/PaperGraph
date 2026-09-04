<template>
  <div class="pdf-viewer">
    <div class="pdf-toolbar" aria-label="PDF 阅读工具栏">
      <a-button size="small" :disabled="currentPage <= 1 || loading" @click="gotoPage(currentPage - 1)">
        上一页
      </a-button>
      <label class="pdf-page-control">
        <input
          ref="pageInputRef"
          v-model.number="pageInput"
          class="pdf-page-input"
          type="number"
          min="1"
          :max="pageCount || 1"
          :disabled="loading || pageCount === 0"
          @keydown.enter.prevent="gotoInputPage"
        />
        <span>/ {{ pageCount || '—' }}</span>
      </label>
      <a-button
        size="small"
        :disabled="loading || pageCount === 0"
        @click="gotoPage(pageInput)"
      >
        跳转
      </a-button>
      <a-button size="small" :disabled="currentPage >= pageCount || loading" @click="gotoPage(currentPage + 1)">
        下一页
      </a-button>
      <span class="pdf-toolbar__separator" aria-hidden="true"></span>
      <a-button size="small" :disabled="zoom <= 0.5 || loading" @click="setZoom(zoom - 0.1)">缩小</a-button>
      <span class="pdf-zoom">{{ Math.round(zoom * 100) }}%</span>
      <a-button size="small" :disabled="zoom >= 2.5 || loading" @click="setZoom(zoom + 0.1)">放大</a-button>
    </div>

    <div ref="scrollRef" class="pdf-scroll" @scroll.passive="onScroll">
      <div
        v-for="pageNumber in pages"
        :key="pageNumber"
        :ref="(el) => setPageElement(el, pageNumber)"
        class="pdf-page"
        :data-page="pageNumber"
        :style="{ minHeight: `${pagePlaceholderHeight}px` }"
      >
        <canvas :ref="(el) => setCanvasElement(el, pageNumber)" class="pdf-canvas"></canvas>
        <span class="pdf-page__number">第 {{ pageNumber }} 页</span>
      </div>
    </div>

    <div v-if="loading" class="pdf-state pdf-state--overlay">
      <a-spin tip="正在解析并渲染 PDF…" />
    </div>
    <div v-else-if="error" class="pdf-state pdf-error">
      <strong>PDF 加载失败</strong>
      <span>{{ error }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import {
  getDocument,
  GlobalWorkerOptions,
  type PDFDocumentLoadingTask,
  type PDFDocumentProxy,
  type RenderTask,
} from 'pdfjs-dist'
import pdfWorkerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
  type ComponentPublicInstance,
} from 'vue'

GlobalWorkerOptions.workerSrc = pdfWorkerUrl

const props = withDefaults(defineProps<{ src: string; page?: number }>(), {
  page: 1,
})
const emit = defineEmits<{ loaded: []; error: [message: string] }>()

const scrollRef = ref<HTMLElement | null>(null)
const pageInputRef = ref<HTMLInputElement | null>(null)
const loading = ref(false)
const error = ref('')
const pages = ref<number[]>([])
const pageCount = ref(0)
const currentPage = ref(1)
const pageInput = ref(1)
const pagePlaceholderHeight = ref(480)
const pageAspectRatio = ref(842 / 595)
const zoom = ref(1)

const pageElements = new Map<number, HTMLElement>()
const canvasElements = new Map<number, HTMLCanvasElement>()
const visiblePages = new Set<number>()
const renderTasks = new Map<number, RenderTask>()
const renderedKeys = new Map<number, string>()

let loadingTask: PDFDocumentLoadingTask | null = null
let pdfDocument: PDFDocumentProxy | null = null
let intersectionObserver: IntersectionObserver | null = null
let resizeObserver: ResizeObserver | null = null
let resizeTimer: number | null = null
let scrollFrame: number | null = null
let loadGeneration = 0
let loadedEmitted = false

const asElement = (
  value: Element | ComponentPublicInstance | null,
): Element | null => {
  if (!value) return null
  return value instanceof Element ? value : value.$el
}

const setPageElement = (
  value: Element | ComponentPublicInstance | null,
  pageNumber: number,
) => {
  const element = asElement(value)
  if (!(element instanceof HTMLElement)) {
    const previous = pageElements.get(pageNumber)
    if (previous) intersectionObserver?.unobserve(previous)
    pageElements.delete(pageNumber)
    return
  }
  pageElements.set(pageNumber, element)
  intersectionObserver?.observe(element)
}

const setCanvasElement = (
  value: Element | ComponentPublicInstance | null,
  pageNumber: number,
) => {
  const element = asElement(value)
  if (element instanceof HTMLCanvasElement) canvasElements.set(pageNumber, element)
  else canvasElements.delete(pageNumber)
}

const cancelRenders = () => {
  for (const task of renderTasks.values()) task.cancel()
  renderTasks.clear()
  renderedKeys.clear()
}

const updatePagePlaceholderHeight = () => {
  const container = scrollRef.value
  if (!container) return
  const availableWidth = Math.max(240, container.clientWidth - 40)
  pagePlaceholderHeight.value = Math.max(
    480,
    Math.floor(availableWidth * zoom.value * pageAspectRatio.value),
  )
}

const renderPage = async (pageNumber: number) => {
  const document = pdfDocument
  const canvas = canvasElements.get(pageNumber)
  const container = scrollRef.value
  if (!document || !canvas || !container || pageNumber < 1 || pageNumber > document.numPages) return

  const page = await document.getPage(pageNumber)
  const naturalViewport = page.getViewport({ scale: 1 })
  const availableWidth = Math.max(240, container.clientWidth - 40)
  const fitScale = availableWidth / naturalViewport.width
  const renderScale = fitScale * zoom.value
  const viewport = page.getViewport({ scale: renderScale })
  const outputScale = Math.min(window.devicePixelRatio || 1, 2)
  const renderKey = `${Math.round(viewport.width)}x${Math.round(viewport.height)}@${outputScale}`
  if (renderedKeys.get(pageNumber) === renderKey) return

  renderTasks.get(pageNumber)?.cancel()
  const context = canvas.getContext('2d', { alpha: false })
  if (!context) throw new Error('当前浏览器无法创建 PDF 画布')

  canvas.width = Math.max(1, Math.floor(viewport.width * outputScale))
  canvas.height = Math.max(1, Math.floor(viewport.height * outputScale))
  canvas.style.width = `${Math.floor(viewport.width)}px`
  canvas.style.height = `${Math.floor(viewport.height)}px`

  const task = page.render({
    canvasContext: context,
    viewport,
    transform: outputScale === 1 ? undefined : [outputScale, 0, 0, outputScale, 0, 0],
  })
  renderTasks.set(pageNumber, task)
  try {
    await task.promise
    renderedKeys.set(pageNumber, renderKey)
    if (!loadedEmitted) {
      loadedEmitted = true
      emit('loaded')
    }
  } catch (reason) {
    if ((reason as { name?: string })?.name !== 'RenderingCancelledException') throw reason
  } finally {
    if (renderTasks.get(pageNumber) === task) renderTasks.delete(pageNumber)
  }
}

const setupIntersectionObserver = () => {
  intersectionObserver?.disconnect()
  intersectionObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const pageNumber = Number((entry.target as HTMLElement).dataset.page || 0)
        if (!pageNumber) continue
        if (entry.isIntersecting) {
          visiblePages.add(pageNumber)
          void renderPage(pageNumber).catch(showRenderError)
        } else {
          visiblePages.delete(pageNumber)
        }
      }
    },
    {
      root: scrollRef.value,
      rootMargin: '700px 0px',
      threshold: 0.01,
    },
  )
  for (const element of pageElements.values()) intersectionObserver.observe(element)
}

const showRenderError = (reason: unknown) => {
  if ((reason as { name?: string })?.name === 'RenderingCancelledException') return
  const message = reason instanceof Error ? reason.message : String(reason)
  error.value = message || '未知渲染错误'
  emit('error', error.value)
}

const cleanupDocument = async () => {
  cancelRenders()
  intersectionObserver?.disconnect()
  visiblePages.clear()
  const oldTask = loadingTask
  const oldDocument = pdfDocument
  loadingTask = null
  pdfDocument = null
  if (oldTask) await oldTask.destroy().catch(() => undefined)
  else if (oldDocument) await oldDocument.destroy().catch(() => undefined)
}

const loadPdf = async () => {
  const generation = ++loadGeneration
  await cleanupDocument()
  pages.value = []
  pageCount.value = 0
  currentPage.value = Math.max(1, Math.floor(props.page || 1))
  pageInput.value = currentPage.value
  pagePlaceholderHeight.value = 480
  pageAspectRatio.value = 842 / 595
  error.value = ''
  loadedEmitted = false
  if (!props.src) {
    loading.value = false
    return
  }

  loading.value = true
  try {
    const task = getDocument({ url: props.src })
    loadingTask = task
    const document = await task.promise
    if (generation !== loadGeneration) {
      await document.destroy()
      return
    }
    pdfDocument = document
    pageCount.value = document.numPages
    const firstPage = await document.getPage(1)
    const firstViewport = firstPage.getViewport({ scale: 1 })
    pageAspectRatio.value = firstViewport.height / firstViewport.width
    updatePagePlaceholderHeight()
    pages.value = Array.from({ length: document.numPages }, (_, index) => index + 1)
    currentPage.value = Math.min(currentPage.value, document.numPages)
    pageInput.value = currentPage.value
    await nextTick()
    setupIntersectionObserver()
    await renderPage(currentPage.value)
    await nextTick()
    pageElements.get(currentPage.value)?.scrollIntoView({ block: 'start' })
  } catch (reason) {
    if (generation !== loadGeneration) return
    const message = reason instanceof Error ? reason.message : String(reason)
    error.value = message || '无法解析该 PDF 文件'
    emit('error', error.value)
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

const gotoPage = async (pageNumber: number) => {
  if (!pageCount.value) return
  const target = Math.min(pageCount.value, Math.max(1, Math.floor(Number(pageNumber) || 1)))
  currentPage.value = target
  pageInput.value = target
  await nextTick()
  await renderPage(target).catch(showRenderError)
  await nextTick()
  const container = scrollRef.value
  const element = pageElements.get(target)
  if (!container || !element) return
  const containerRect = container.getBoundingClientRect()
  const elementRect = element.getBoundingClientRect()
  const targetTop = Math.max(
    0,
    container.scrollTop + elementRect.top - containerRect.top - 12,
  )
  container.scrollTop = targetTop
}

const gotoInputPage = (event: Event) => {
  const value = Number((event.currentTarget as HTMLInputElement | null)?.value)
  void gotoPage(value)
}

const setZoom = (value: number) => {
  zoom.value = Math.min(2.5, Math.max(0.5, Math.round(value * 10) / 10))
  updatePagePlaceholderHeight()
  cancelRenders()
  const targets = visiblePages.size ? [...visiblePages] : [currentPage.value]
  for (const pageNumber of targets) void renderPage(pageNumber).catch(showRenderError)
}

const onScroll = () => {
  if (scrollFrame != null) cancelAnimationFrame(scrollFrame)
  scrollFrame = requestAnimationFrame(() => {
    scrollFrame = null
    const root = scrollRef.value
    if (!root || pageElements.size === 0) return
    const rootTop = root.getBoundingClientRect().top
    let nearest = currentPage.value
    let nearestDistance = Number.POSITIVE_INFINITY
    for (const [pageNumber, element] of pageElements) {
      const distance = Math.abs(element.getBoundingClientRect().top - rootTop - 12)
      if (distance < nearestDistance) {
        nearestDistance = distance
        nearest = pageNumber
      }
    }
    currentPage.value = nearest
    if (document.activeElement !== pageInputRef.value) {
      pageInput.value = nearest
    }
  })
}

defineExpose({ gotoPage })

watch(() => props.src, () => { void loadPdf() }, { immediate: true })
watch(
  () => props.page,
  (page) => {
    if (pageCount.value && page && page > 0) void gotoPage(page)
  },
)

onMounted(() => {
  if (!scrollRef.value) return
  resizeObserver = new ResizeObserver(() => {
    if (resizeTimer != null) window.clearTimeout(resizeTimer)
    resizeTimer = window.setTimeout(() => {
      updatePagePlaceholderHeight()
      cancelRenders()
      const targets = visiblePages.size ? [...visiblePages] : [currentPage.value]
      for (const pageNumber of targets) void renderPage(pageNumber).catch(showRenderError)
    }, 120)
  })
  resizeObserver.observe(scrollRef.value)
})

onBeforeUnmount(() => {
  loadGeneration += 1
  if (resizeTimer != null) window.clearTimeout(resizeTimer)
  if (scrollFrame != null) cancelAnimationFrame(scrollFrame)
  resizeObserver?.disconnect()
  void cleanupDocument()
})
</script>

<style scoped>
.pdf-viewer {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 0;
  overflow: hidden;
  background: #e8e8eb;
}
.pdf-toolbar {
  z-index: 2;
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 44px;
  padding: 6px 12px;
  background: rgba(255, 255, 255, 0.96);
  border-bottom: 1px solid var(--pg-border);
  box-shadow: 0 1px 4px rgba(24, 24, 27, 0.08);
}
.pdf-page-control {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--pg-text-secondary);
  font-size: 12px;
}
.pdf-page-input {
  width: 48px;
  height: 25px;
  padding: 0 4px;
  text-align: center;
  border: 1px solid var(--pg-border);
  border-radius: 5px;
  background: #fff;
  color: var(--pg-text);
}
.pdf-toolbar__separator {
  width: 1px;
  height: 20px;
  margin: 0 2px;
  background: var(--pg-border);
}
.pdf-zoom {
  min-width: 42px;
  text-align: center;
  color: var(--pg-text-secondary);
  font-size: 12px;
}
.pdf-scroll {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
  padding: 18px 20px 40px;
}
.pdf-page {
  position: relative;
  display: flex;
  justify-content: center;
  width: fit-content;
  min-width: min(100%, 240px);
  min-height: 480px;
  margin: 0 auto 18px;
  background: #fff;
  box-shadow: 0 2px 12px rgba(24, 24, 27, 0.18);
}
.pdf-canvas {
  display: block;
  max-width: none;
  background: #fff;
}
.pdf-page__number {
  position: absolute;
  right: 8px;
  bottom: 6px;
  padding: 2px 6px;
  color: #71717a;
  background: rgba(255, 255, 255, 0.85);
  border-radius: 4px;
  font-size: 10px;
  pointer-events: none;
}
.pdf-state {
  position: absolute;
  inset: 45px 0 0;
  z-index: 3;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 24px;
  background: var(--pg-bg-soft);
  color: var(--pg-text-secondary);
}
.pdf-state--overlay {
  background: rgba(248, 248, 250, 0.9);
}
.pdf-error {
  flex-direction: column;
  color: #cf1322;
}
</style>
