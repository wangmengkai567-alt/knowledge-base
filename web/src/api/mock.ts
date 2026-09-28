import type { AxiosAdapter, AxiosResponse } from 'axios'
import type {
  ApiResponse,
  Pagination,
  KnowledgeBase,
  KnowledgeBasePayload,
  DocItem,
  DocDetail,
  Chunk,
  SearchHit,
  RagSummary,
  RagSource,
  User,
  LoginRequest,
  LoginResponse,
} from '@/types'
import {
  KbStatus,
  ParseStatus,
  IndexStatus,
  EmbeddingStatus,
} from '@/types'

/* ============================== 工具 ============================== */
const delay = 260
const now = () => new Date().toISOString()
const daysAgo = (d: number) => new Date(Date.now() - d * 86400000).toISOString()

function ok<T>(data: T, pagination?: Pagination): ApiResponse<T> {
  return { code: '000000', message: 'success', data, pagination }
}

function paginate<T>(list: T[], page = 1, pageSize = 20): { list: T[]; pagination: Pagination } {
  const total = list.length
  const totalPages = Math.max(1, Math.ceil(total / pageSize))
  const start = (page - 1) * pageSize
  return {
    list: list.slice(start, start + pageSize),
    pagination: { page, pageSize, total, totalPages },
  }
}

/* ============================== 种子数据 ============================== */
interface DocSeed {
  title: string
  docType: string
  docTypeDesc: string
  tags: string[]
  chunks: { heading: string; content: string; keywords: string[] }[]
}

const KB_SEEDS: { name: string; description: string; icon: string; docs: DocSeed[] }[] = [
  {
    name: '技术文档库',
    description: '系统架构、微服务、中间件与运维相关的技术文档集合',
    icon: 'cpu',
    docs: [
      {
        title: '系统架构设计规范.pdf',
        docType: 'manual',
        docTypeDesc: '技术规范',
        tags: ['架构', '微服务', 'SpringBoot'],
        chunks: [
          { heading: '## 1. 总体架构', content: '系统采用微服务架构，包含用户服务、权限服务、检索服务与网关层，各服务通过注册中心进行服务发现。', keywords: ['微服务', '架构', '网关'] },
          { heading: '## 2. 服务拆分原则', content: '服务拆分遵循单一职责与高内聚低耦合原则，按业务域划分边界，避免跨库事务。', keywords: ['服务拆分', '微服务'] },
          { heading: '## 3. 技术栈', content: '后端基于 SpringBoot 3 与 Java 21，网关使用 Spring Cloud Gateway，鉴权基于 JWT。', keywords: ['SpringBoot', 'Java', 'JWT'] },
        ],
      },
      {
        title: '微服务治理白皮书.docx',
        docType: 'manual',
        docTypeDesc: '技术规范',
        tags: ['微服务', '熔断', '限流'],
        chunks: [
          { heading: '## 1. 熔断与降级', content: '当依赖服务错误率超过阈值时触发熔断，返回降级结果，保护核心链路可用性。', keywords: ['熔断', '降级', '微服务'] },
          { heading: '## 2. 限流策略', content: '采用令牌桶限流，按租户维度分配配额，超出额度返回 429。', keywords: ['限流', '令牌桶'] },
        ],
      },
      {
        title: 'API网关接入指南.md',
        docType: 'api_doc',
        docTypeDesc: '接口文档',
        tags: ['网关', 'API', 'JWT'],
        chunks: [
          { heading: '## 接入流程', content: '在网关注册路由后，请求经 JWT 鉴权再转发到后端服务，统一注入 tenantId。', keywords: ['网关', 'JWT', 'API'] },
        ],
      },
      {
        title: '数据库分库分表方案.pdf',
        docType: 'manual',
        docTypeDesc: '技术规范',
        tags: ['数据库', '分库分表', 'MySQL'],
        chunks: [
          { heading: '## 分片策略', content: '按 tenant_id 做分库，按主键哈希做分表，单表控制在 500 万行以内。', keywords: ['分库分表', '数据库'] },
        ],
      },
    ],
  },
  {
    name: '产品文档库',
    description: '产品需求、用户手册与功能说明文档',
    icon: 'goods',
    docs: [
      {
        title: '产品需求文档PRD.pdf',
        docType: 'manual',
        docTypeDesc: '产品文档',
        tags: ['PRD', '需求'],
        chunks: [
          { heading: '## 背景', content: '面向企业内部的智能知识检索平台，解决文档分散、检索效率低的问题。', keywords: ['知识检索', '产品'] },
          { heading: '## 核心功能', content: '支持关键词与自然语言提问，返回文档片段、匹配度与 AI 生成的检索摘要。', keywords: ['自然语言', '检索摘要', '匹配度'] },
        ],
      },
      {
        title: '用户操作手册.docx',
        docType: 'manual',
        docTypeDesc: '产品文档',
        tags: ['手册', '操作'],
        chunks: [
          { heading: '## 快速上手', content: '在搜索框输入问题后点击检索，左侧可筛选知识库与文件类型，右侧查看 AI 摘要。', keywords: ['搜索', '筛选', '摘要'] },
        ],
      },
      {
        title: '功能迭代路线图.md',
        docType: 'meeting_minutes',
        docTypeDesc: '会议纪要',
        tags: ['路线图'],
        chunks: [
          { heading: '## Q3 规划', content: 'Q3 重点建设 RAG 问答与多知识库联合检索能力。', keywords: ['RAG', '检索'] },
        ],
      },
    ],
  },
  {
    name: '项目资料库',
    description: '项目复盘、需求调研与竞品分析等资料',
    icon: 'folder',
    docs: [
      {
        title: 'Q3季度复盘报告.pdf',
        docType: 'meeting_minutes',
        docTypeDesc: '会议纪要',
        tags: ['复盘', '季度'],
        chunks: [
          { heading: '## 成果', content: '本季度完成检索服务重构，P95 延迟从 120ms 降至 80ms。', keywords: ['检索', '延迟'] },
        ],
      },
      {
        title: '客户需求调研.docx',
        docType: 'manual',
        docTypeDesc: '调研',
        tags: ['需求', '客户'],
        chunks: [
          { heading: '## 痛点', content: '客户反馈跨知识库检索体验割裂，希望统一入口与企业级搜索体验。', keywords: ['跨知识库', '检索'] },
        ],
      },
      {
        title: '竞品分析报告.md',
        docType: 'meeting_minutes',
        docTypeDesc: '分析',
        tags: ['竞品'],
        chunks: [
          { heading: '## 对比', content: '对标 Notion 知识库与 Algolia 搜索体验，强化匹配度可视化与摘要。', keywords: ['Algolia', 'Notion', '摘要'] },
        ],
      },
    ],
  },
  {
    name: '制度文件库',
    description: '公司制度、规范与安全合规类文件',
    icon: 'document',
    docs: [
      {
        title: '信息安全管理制度.pdf',
        docType: 'manual',
        docTypeDesc: '制度',
        tags: ['安全', '合规'],
        chunks: [
          { heading: '## 数据分级', content: '数据按敏感程度分为公开、内部、机密三级，机密数据禁止离开内网。', keywords: ['数据分级', '安全'] },
        ],
      },
      {
        title: '代码评审规范.docx',
        docType: 'manual',
        docTypeDesc: '规范',
        tags: ['代码', '评审'],
        chunks: [
          { heading: '## 评审清单', content: '评审关注可读性、边界条件、安全与单元测试覆盖率，需至少一名 Reviewer 通过。', keywords: ['代码评审', '规范'] },
        ],
      },
      {
        title: '数据分级分类指南.md',
        docType: 'manual',
        docTypeDesc: '指南',
        tags: ['数据', '分级'],
        chunks: [
          { heading: '## 分类标准', content: '按业务属性划分数据类别，结合分级策略执行差异化访问控制。', keywords: ['数据分级', '分类'] },
        ],
      },
    ],
  },
]

/* ============================== 内存存储 ============================== */
let KB_SEQ = 100
let DOC_SEQ = 1000
class KBStore {
  kbs: KnowledgeBase[] = []
  docs: Map<number, DocItem[]> = new Map()
  chunks: Map<number, Chunk[]> = new Map()

  constructor() {
    KB_SEEDS.forEach((seed, i) => {
      const kbId = i + 1
      this.kbs.push({
        kbId,
        name: seed.name,
        description: seed.description,
        icon: seed.icon,
        docCount: seed.docs.length,
        chunkCount: seed.docs.reduce((s, d) => s + d.chunks.length, 0),
        embeddingModel: 'deepseek-embedding',
        chunkStrategy: 'recursive',
        chunkSize: 500,
        chunkOverlap: 50,
        status: KbStatus.Normal,
        statusDesc: '正常',
        createdBy: { userId: 1, nickname: '管理员' },
        createdAt: daysAgo(20 - i * 3),
        updatedAt: daysAgo(i),
      })
      const docList: DocItem[] = seed.docs.map((d, di) => {
        const docId = DOC_SEQ++
        const chunkList: Chunk[] = d.chunks.map((c, ci) => ({
          chunkId: docId * 100 + ci,
          chunkIndex: ci,
          content: c.content,
          charCount: c.content.length,
          embeddingStatus: EmbeddingStatus.Done,
          embeddingStatusDesc: '已入库',
          createdAt: daysAgo(i + di),
        }))
        this.chunks.set(docId, chunkList)
        return {
          docId,
          kbId,
          title: d.title.replace(/\.[a-z0-9]+$/i, ''),
          docType: d.docType,
          docTypeDesc: d.docTypeDesc,
          tags: d.tags,
          charCount: d.chunks.reduce((s, c) => s + c.content.length, 0),
          fileSize: d.chunks.reduce((s, c) => s + c.content.length, 0),
          chunkCount: d.chunks.length,
          parseStatus: ParseStatus.Parsed,
          parseStatusDesc: '解析成功',
          indexStatus: IndexStatus.Indexed,
          indexStatusDesc: '已索引',
          version: 1,
          language: 'zh',
          createdBy: { userId: 1, nickname: '管理员' },
          createdAt: daysAgo(i + di),
          indexedAt: daysAgo(i),
        }
      })
      this.docs.set(kbId, docList)
    })
  }
}
const store = new KBStore()

const DEFAULT_USER: User = {
  userId: 1,
  email: 'admin@company.com',
  nickname: '管理员',
  avatarUrl: '',
  globalRole: 1,
  globalRoleDesc: '平台管理员',
  tenantId: 5,
  tenantName: '示例企业',
  kbCount: store.kbs.length,
  lastLoginAt: now(),
}

/* ============================== 检索逻辑 ============================== */
function scoreChunk(content: string, terms: string[]): number {
  if (!terms.length) return 0.45
  const lower = content.toLowerCase()
  let hits = 0
  for (const t of terms) {
    if (t && lower.includes(t.toLowerCase())) hits++
  }
  return Math.min(0.99, 0.5 + 0.13 * hits)
}

function tokenize(q: string): string[] {
  return q
    .split(/[\s,，。、？?！!；;：:]+/)
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}

function toBackendKb(kb: KnowledgeBase) {
  return {
    id: kb.kbId,
    name: kb.name,
    description: kb.description,
    icon: kb.icon || 'folder',
    status: kb.status,
    owner_id: kb.createdBy?.userId ?? 1,
    embedding_model: kb.embeddingModel || '',
    chunk_strategy: kb.chunkStrategy || 'recursive',
    chunk_size: kb.chunkSize || 500,
    chunk_overlap: kb.chunkOverlap || 50,
    doc_count: kb.docCount,
    chunk_count: kb.chunkCount,
    created_at: kb.createdAt,
    updated_at: kb.updatedAt,
  }
}

/* ============================== Mock 路由 ============================== */
type Ctx = { url: string; method: string; data: any; params: any }

function handle(ctx: Ctx): any {
  const { url, method, data, params } = ctx
  const p = (s: string) => new RegExp(s)
  const m = url.match(p('^/v1/(.+)$'))
  const path = m ? m[1] : url
  const pm = method.toLowerCase()

  // 登录
  if (pm === 'post' && path === 'users/login') {
    const body = data as LoginRequest
    const resp: LoginResponse = {
      accessToken: 'mock-token-' + Date.now(),
      refreshToken: 'mock-refresh-' + Date.now(),
      expiresIn: 7200,
      user: { ...DEFAULT_USER, email: body?.email || DEFAULT_USER.email },
    }
    return ok(resp)
  }
  // 当前用户
  if (pm === 'get' && path === 'users/me') return ok(DEFAULT_USER)

  // 知识库列表（真实后端契约）
  if (pm === 'get' && path === 'knowledge/bases') {
    return {
      knowledge_bases: store.kbs.map(toBackendKb),
      total: store.kbs.length,
    }
  }
  if (pm === 'post' && path === 'knowledge/bases') {
    const body = data as KnowledgeBasePayload
    const kbId = ++KB_SEQ
    const kb: KnowledgeBase = {
      kbId,
      name: body.name,
      description: body.description || '',
      icon: body.icon || 'folder',
      docCount: 0,
      chunkCount: 0,
      embeddingModel: body.embeddingModel || 'deepseek-embedding',
      chunkStrategy: body.chunkStrategy || 'recursive',
      chunkSize: body.chunkSize || 500,
      chunkOverlap: body.chunkOverlap || 50,
      status: KbStatus.Normal,
      statusDesc: '正常',
      createdBy: { userId: 1, nickname: '管理员' },
      createdAt: now(),
      updatedAt: now(),
    }
    store.kbs.unshift(kb)
    store.docs.set(kbId, [])
    return toBackendKb(kb)
  }
  let kbm = path.match(p('^knowledge/bases/(\\d+)$'))
  if (kbm && pm === 'get') {
    const kb = store.kbs.find((k) => k.kbId === Number(kbm![1]))
    if (!kb) return fail('000004', '知识库不存在')
    return toBackendKb(kb)
  }
  if (kbm && pm === 'patch') {
    const kb = store.kbs.find((k) => k.kbId === Number(kbm![1]))
    if (!kb) return fail('000004', '知识库不存在')
    Object.assign(kb, { ...data, updatedAt: now() })
    return toBackendKb(kb)
  }
  if (kbm && pm === 'delete') {
    const kbId = Number(kbm[1])
    store.kbs = store.kbs.filter((k) => k.kbId !== kbId)
    store.docs.delete(kbId)
    return null
  }

  // 知识库列表（旧 mock 路径，保留兼容）
  if (pm === 'get' && path === 'kbs') {
    let list = [...store.kbs]
    if (params?.keyword) list = list.filter((k) => k.name.includes(params.keyword))
    if (params?.status) list = list.filter((k) => k.status === Number(params.status))
    list.sort((a, b) => (a.updatedAt < b.updatedAt ? 1 : -1))
    const pg = paginate(list, params?.page || 1, params?.pageSize || 20)
    return ok(pg.list, pg.pagination)
  }
  // 创建知识库
  if (pm === 'post' && path === 'kbs') {
    const body = data as KnowledgeBasePayload
    const kbId = ++KB_SEQ
    const kb: KnowledgeBase = {
      kbId,
      name: body.name,
      description: body.description || '',
      icon: body.icon || 'folder',
      docCount: 0,
      chunkCount: 0,
      embeddingModel: body.embeddingModel || 'deepseek-embedding',
      chunkStrategy: body.chunkStrategy || 'recursive',
      chunkSize: body.chunkSize || 500,
      chunkOverlap: body.chunkOverlap || 50,
      status: KbStatus.Normal,
      statusDesc: '正常',
      createdBy: { userId: 1, nickname: '管理员' },
      createdAt: now(),
      updatedAt: now(),
    }
    store.kbs.unshift(kb)
    store.docs.set(kbId, [])
    return ok(kb)
  }
  // 知识库详情
  let km = path.match(p('^kbs/(\\d+)$'))
  if (km && pm === 'get') {
    const kb = store.kbs.find((k) => k.kbId === Number(km![1]))
    if (!kb) return fail('000004', '知识库不存在')
    return ok(kb)
  }
  // 更新知识库
  if (km && pm === 'patch') {
    const kb = store.kbs.find((k) => k.kbId === Number(km![1]))
    if (!kb) return fail('000004', '知识库不存在')
    Object.assign(kb, { ...data, updatedAt: now() })
    return ok(kb)
  }
  // 删除知识库
  if (km && pm === 'delete') {
    store.kbs = store.kbs.filter((k) => k.kbId !== Number(km![1]))
    return ok({ kbId: Number(km[1]), deletedAt: now() })
  }

  // 文档列表
  let dm = path.match(p('^knowledge/bases/(\\d+)/documents$'))
  if (dm && pm === 'get') {
    const kbId = Number(dm[1])
    let list = [...(store.docs.get(kbId) || [])]
    if (params?.keyword) list = list.filter((d) => d.title.includes(params.keyword))
    if (params?.parseStatus) list = list.filter((d) => d.parseStatus === Number(params.parseStatus))
    if (params?.indexStatus) list = list.filter((d) => d.indexStatus === Number(params.indexStatus))
    if (params?.docType) list = list.filter((d) => d.docType === params.docType)
    list.sort((a, b) => (a.createdAt < b.createdAt ? 1 : -1))
    const pg = paginate(list, params?.page || 1, params?.pageSize || 20)
    return ok(pg.list, pg.pagination)
  }
  // 上传文档（multipart，mock 仅返回占位）
  if (dm && pm === 'post') {
    const kbId = Number(dm[1])
    const docId = DOC_SEQ++
    const doc: DocItem = {
      docId,
      kbId,
      title: '新上传文档',
      docType: 'generic',
      docTypeDesc: '通用',
      tags: [],
      charCount: 0,
      fileSize: 0,
      chunkCount: 0,
      parseStatus: ParseStatus.Pending,
      parseStatusDesc: '待解析',
      indexStatus: IndexStatus.None,
      indexStatusDesc: '未索引',
      version: 1,
      language: 'zh',
      createdBy: { userId: 1, nickname: '管理员' },
      createdAt: now(),
    }
    store.docs.get(kbId)?.unshift(doc)
    const kb = store.kbs.find((k) => k.kbId === kbId)
    if (kb) kb.docCount++
    return ok(doc)
  }
  // 文档详情
  let ddm = path.match(p('^knowledge/bases/(\\d+)/documents/(\\d+)$'))
  if (ddm && pm === 'get') {
    const kbId = Number(ddm[1])
    const docId = Number(ddm[2])
    const doc = (store.docs.get(kbId) || []).find((d) => d.docId === docId)
    if (!doc) return fail('000004', '文档不存在')
    const detail: DocDetail = {
      ...doc,
      sourceUrl: '',
      parseErrorMsg: null,
      indexErrorMsg: null,
      parsedAt: doc.createdAt,
      file: {
        fileId: doc.docId,
        originalName: doc.title + '.pdf',
        mimeType: 'application/pdf',
        fileSize: doc.charCount * 12,
        fileSizeDesc: ((doc.charCount * 12) / 1024).toFixed(0) + ' KB',
      },
      updatedAt: doc.createdAt,
    }
    return ok(detail)
  }
  // 删除文档
  if (ddm && pm === 'delete') {
    const kbId = Number(ddm[1])
    const docId = Number(ddm[2])
    const list = store.docs.get(kbId) || []
    store.docs.set(
      kbId,
      list.filter((d) => d.docId !== docId),
    )
    return ok({ docId, deletedAt: now() })
  }
  let pem = path.match(p('^knowledge/bases/(\\d+)/documents/(\\d+)/(parse|embed)$'))
  if (pem && pm === 'post') {
    const kbId = Number(pem[1])
    const docId = Number(pem[2])
    const doc = (store.docs.get(kbId) || []).find((d) => d.docId === docId)
    if (!doc) return fail('000004', '文档不存在')
    doc.parseStatus = ParseStatus.Parsed
    doc.parseStatusDesc = '解析成功'
    doc.indexStatus = IndexStatus.Indexed
    doc.indexStatusDesc = '已索引'
    return {
      id: doc.docId,
      knowledge_base_id: kbId,
      filename: doc.title,
      file_size: doc.charCount,
      mime_type: 'text/plain',
      status: 2,
      parsed_content: '',
      error_message: null,
      chunk_count: doc.chunkCount,
      embedding_pending_count: 0,
      embedding_failed_count: 0,
      embedding_completed_count: doc.chunkCount,
      created_at: doc.createdAt,
    }
  }
  // 文档分块
  let cm = path.match(p('^knowledge/bases/(\\d+)/documents/(\\d+)/chunks$'))
  if (cm && pm === 'get') {
    const docId = Number(cm[2])
    const list = [...(store.chunks.get(docId) || [])].sort((a, b) => a.chunkIndex - b.chunkIndex)
    const pg = paginate(list, params?.page || 1, params?.pageSize || 50)
    return ok(pg.list, pg.pagination)
  }
  // 检索
  let rm = path.match(p('^knowledge/bases/(\\d+)/retrieve$'))
  if (rm && pm === 'post') {
    const kbId = Number(rm[1])
    const query = (data?.query || '') as string
    const topK = data?.topK || 10
    const terms = tokenize(query)
    const docs = store.docs.get(kbId) || []
    const hits: SearchHit[] = []
    for (const doc of docs) {
      const chunks = store.chunks.get(doc.docId) || []
      for (const c of chunks) {
        const score = scoreChunk(c.content, terms)
        hits.push({
          chunkId: c.chunkId,
          docId: doc.docId,
          kbId,
          docName: doc.title + '.pdf',
          docTitle: doc.title,
          content: c.content,
          position: c.chunkIndex,
          fileType: 'pdf',
          score,
          keywords: terms.filter((t) => t && c.content.toLowerCase().includes(t.toLowerCase())),
          updatedAt: doc.createdAt,
        })
      }
    }
    hits.sort((a, b) => b.score - a.score)
    return ok({
      query,
      knowledge_base_id: kbId,
      results: hits.slice(0, topK),
      total: hits.length,
    })
  }
  // RAG 摘要
  let ragm = path.match(p('^knowledge/bases/(\\d+)/rag/query$'))
  if (ragm && pm === 'post') {
    const kbId = Number(ragm[1])
    const query = (data?.query || '') as string
    const topK = data?.topK || 5
    const terms = tokenize(query)
    const docs = store.docs.get(kbId) || []
    const all: SearchHit[] = []
    for (const doc of docs) {
      for (const c of store.chunks.get(doc.docId) || []) {
        all.push({
          chunkId: c.chunkId,
          docId: doc.docId,
          kbId,
          docName: doc.title + '.pdf',
          docTitle: doc.title,
          content: c.content,
          position: c.chunkIndex,
          fileType: 'pdf',
          score: scoreChunk(c.content, terms),
          keywords: [],
          updatedAt: doc.createdAt,
        })
      }
    }
    all.sort((a, b) => b.score - a.score)
    const top = all.slice(0, topK)
    const sources: RagSource[] = top.map((h) => ({
      chunkId: h.chunkId,
      content: h.content.slice(0, 200),
      score: h.score,
      documentId: h.docId,
      documentFilename: h.docName,
      kbId,
    }))
    const answer = `根据「${store.kbs.find((k) => k.kbId === kbId)?.name || '知识库'}」的检索结果，针对“${query}”整理如下：\n\n核心结论：${
      top[0]?.content || '暂无直接相关文档，建议补充知识库内容。'
    }\n\n关键步骤：\n1. 明确检索目标与范围；\n2. 在搜索框输入关键词或自然语言问题；\n3. 结合右侧引用来源交叉验证结论。\n\n注意事项：摘要由 AI 基于检索片段生成，关键决策请以原文为准，并注意数据分级与权限要求。`
    const summary: RagSummary = {
      answer,
      model: 'deepseek-chat',
      sources,
      sourceCount: sources.length,
    }
    return ok(summary)
  }

  return fail('000004', `Mock 未实现接口: ${method.toUpperCase()} ${url}`)
}

function fail(code: string, message: string): ApiResponse<null> {
  return { code, message, data: null }
}

/* ============================== Axios 适配器 ============================== */
export const mockAdapter: AxiosAdapter = (config: any): Promise<AxiosResponse> => {
  return new Promise((resolve) => {
    const url: string = config.url || ''
    let data: any = config.data
    if (typeof data === 'string') {
      try {
        data = JSON.parse(data)
      } catch {
        data = {}
      }
    }
    const params = config.params || {}
    setTimeout(() => {
      const body = handle({ url, method: config.method || 'get', data, params })
      resolve({
        data: body,
        status: 200,
        statusText: 'OK',
        headers: {},
        config,
        request: {},
      })
    }, delay)
  })
}
