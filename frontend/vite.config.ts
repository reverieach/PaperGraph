import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

const antdChunkGroups: Record<string, ReadonlySet<string>> = {
  'antd-data': new Set([
    'empty', 'list', 'pagination', 'table', 'tag',
    'vc-pagination', 'vc-table',
  ]),
  'antd-input': new Set([
    'checkbox', 'dropdown', 'input', 'menu', 'select', 'slider', 'switch',
    'vc-checkbox', 'vc-dropdown', 'vc-input', 'vc-overflow', 'vc-select',
    'vc-slider', 'vc-virtual-list',
  ]),
  'antd-overlay': new Set([
    'message', 'modal', 'notification', 'popconfirm', 'popover', 'tooltip',
    'vc-dialog', 'vc-notification', 'vc-tooltip', 'vc-trigger',
  ]),
  'antd-layout': new Set([
    'alert', 'button', 'card', 'collapse', 'layout', 'skeleton', 'space',
  ]),
  'antd-picker': new Set([
    'calendar', 'date-picker', 'time-picker', 'vc-picker',
  ]),
  'antd-foundation': new Set([
    '_util', 'config-provider', 'locale', 'locale-provider', 'style', 'theme',
    'vc-util', 'version',
  ]),
  'antd-display': new Set([
    'avatar', 'badge', 'breadcrumb', 'col', 'descriptions', 'divider', 'flex',
    'grid', 'icon', 'image', 'progress', 'qrcode', 'result', 'row', 'statistic',
    'steps', 'tabs', 'timeline', 'typography',
  ]),
}

function resolveAntdChunk(id: string): string | undefined {
  const normalizedId = id.replaceAll('\\', '/')
  const marker = '/node_modules/ant-design-vue/es/'
  const markerIndex = normalizedId.indexOf(marker)
  if (markerIndex < 0) return undefined

  const moduleName = normalizedId
    .slice(markerIndex + marker.length)
    .split('/', 1)[0]

  for (const [chunkName, modules] of Object.entries(antdChunkGroups)) {
    if (modules.has(moduleName)) return chunkName
  }
  return 'antd-core'
}

function parsePort(raw: string | undefined, fallback: number): number {
  const n = Number.parseInt(String(raw ?? '').trim(), 10)
  if (!Number.isFinite(n) || n < 1 || n > 65535) return fallback
  return n
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendPort = parsePort(
    process.env.BACKEND_PORT || env.BACKEND_PORT || env.VITE_BACKEND_PORT,
    8000,
  )
  const frontendPort = parsePort(
    process.env.FRONTEND_PORT || env.FRONTEND_PORT || env.VITE_DEV_PORT,
    5173,
  )
  const backend = `http://127.0.0.1:${backendPort}`

  return {
    plugins: [vue()],
    resolve: { alias: { '@': resolve(process.cwd(), 'src') } },
    server: {
      host: '127.0.0.1',
      port: frontendPort,
      strictPort: true,
      open: '/',
      proxy: {
        '/api': { target: backend, changeOrigin: true, timeout: 420000, proxyTimeout: 420000 },
        '/health': { target: backend, changeOrigin: true, timeout: 420000, proxyTimeout: 420000 },
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            const antdChunk = resolveAntdChunk(id)
            if (antdChunk) return antdChunk
            if (id.includes('node_modules/katex')) return 'katex'
          },
        },
      },
    },
  }
})
