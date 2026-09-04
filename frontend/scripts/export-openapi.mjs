import { existsSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const frontendDir = resolve(scriptDir, '..')
const backendDir = resolve(frontendDir, '..', 'backend')
const executable = process.env.PAPERGRAPH_PYTHON?.trim()

if (!executable) {
  throw new Error(
    '无法导出 OpenAPI：请显式设置 PAPERGRAPH_PYTHON，指向完整 RAG 环境的 Python。' +
      '不再回退到 backend/.venv 或系统 Python，以避免生成与实际服务不一致的接口定义。',
  )
}

if (
  (executable.includes('/') || executable.includes('\\')) &&
  !existsSync(executable)
) {
  throw new Error(`无法导出 OpenAPI：PAPERGRAPH_PYTHON 不存在：${executable}`)
}

const code = [
  'import json',
  'from pathlib import Path',
  'from app.api.main import app',
  "Path('../frontend/openapi.json').write_text(" +
    "json.dumps(app.openapi(), ensure_ascii=False, indent=2) + '\\n', " +
    "encoding='utf-8')",
].join('\n')

const result = spawnSync(executable, ['-c', code], {
  cwd: backendDir,
  encoding: 'utf8',
  stdio: 'inherit',
})

if (result.status === 0) process.exit(0)

throw new Error(
  `无法导出 OpenAPI：${result.error?.message || `exit code ${result.status}`}`,
)
