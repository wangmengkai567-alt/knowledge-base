<script setup lang="ts">
import { onMounted, onUnmounted, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { UploadRequestOptions } from 'element-plus'
import { documentApi } from '@/api/document'
import { knowledgeApi } from '@/api/knowledge'
import DocumentList from '@/components/Knowledge/DocumentList.vue'
import { ParseStatus, IndexStatus } from '@/types'
import type { DocItem, KnowledgeBase } from '@/types'

const props = defineProps<{ kbId: string }>()
const router = useRouter()
const kbId = computed(() => Number(props.kbId))

const docs = ref<DocItem[]>([])
const loading = ref(false)
const uploadRef = ref<any>(null)
const kb = ref<KnowledgeBase | null>(null)
const uploadPercent = ref(0)
const uploadPhase = ref<'idle' | 'uploading' | 'processing'>('idle')

let pollTimer: ReturnType<typeof setTimeout> | null = null
const POLL_MS = 2000
const POLL_MAX = 15
let pollCount = 0

function triggerUpload() {
  uploadRef.value?.$el?.querySelector('input')?.click()
}

function needsPoll(list: DocItem[]): boolean {
  return list.some(
    (d) =>
      d.parseStatus === ParseStatus.Processing ||
      d.parseStatus === ParseStatus.Pending ||
      d.indexStatus === IndexStatus.Indexing,
  )
}

function stopPoll() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}

function schedulePoll() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
  if (!needsPoll(docs.value) || pollCount >= POLL_MAX) return
  pollTimer = setTimeout(async () => {
    pollCount += 1
    try {
      docs.value = await documentApi.list(kbId.value, {})
    } catch {
      /* 轮询失败下一轮再试 */
    }
    schedulePoll()
  }, POLL_MS)
}

async function load() {
  loading.value = true
  try {
    const [docList, kbInfo] = await Promise.all([
      documentApi.list(kbId.value, {}),
      knowledgeApi.detail(kbId.value),
    ])
    docs.value = docList
    kb.value = kbInfo
    pollCount = 0
    schedulePoll()
  } finally {
    loading.value = false
  }
}

function customUpload(req: UploadRequestOptions) {
  return runUpload(req.file as File).then(
    () => req.onSuccess?.({} as any),
    (err) => req.onError?.(err),
  )
}

async function runUpload(file: File) {
  uploadPhase.value = 'uploading'
  uploadPercent.value = 0
  try {
    await documentApi.upload(kbId.value, file, {
      onProgress: (p) => {
        uploadPercent.value = p
      },
    })
    uploadPhase.value = 'processing'
    uploadPercent.value = 100
    ElMessage.success('上传成功，正在解析入库')
    await load()
  } finally {
    uploadPhase.value = 'idle'
    uploadPercent.value = 0
  }
}

function view(doc: DocItem) {
  router.push(`/knowledge/${kbId.value}/documents/${doc.docId}`)
}

async function remove(doc: DocItem) {
  await ElMessageBox.confirm(`确定删除文档「${doc.title}」吗？关联向量将异步清理。`, '删除确认', {
    type: 'warning',
  })
  await documentApi.remove(kbId.value, doc.docId)
  ElMessage.success('已删除')
  load()
}

async function removeMany(items: DocItem[]) {
  if (!items.length) return
  await ElMessageBox.confirm(`确定删除选中的 ${items.length} 篇文档吗？关联向量将异步清理。`, '删除确认', {
    type: 'warning',
  })
  for (const doc of items) {
    await documentApi.remove(kbId.value, doc.docId)
  }
  ElMessage.success('已删除')
  load()
}

async function reindex(doc: DocItem) {
  try {
    if (
      doc.parseStatus === ParseStatus.Parsed &&
      (doc.indexStatus === IndexStatus.Failed || doc.indexStatus === IndexStatus.Indexing)
    ) {
      ElMessage.info(`正在重试「${doc.title}」的向量化…`)
      await documentApi.retryEmbed(kbId.value, doc.docId)
    } else {
      ElMessage.info(`正在重新解析「${doc.title}」…`)
      await documentApi.reparse(
        kbId.value,
        doc.docId,
        doc.parseStatus === ParseStatus.Parsed || doc.parseStatus === ParseStatus.Processing,
      )
    }
    ElMessage.success('处理完成')
  } catch {
    /* 请求层已提示错误 */
  }
  await load()
}

async function download(doc: DocItem) {
  try {
    const detail = await documentApi.detail(kbId.value, doc.docId)
    const text = detail.fullContent || ''
    if (!text) {
      ElMessage.warning('这篇文档还没有可下载的解析内容')
      return
    }
    const name = detail.file?.originalName || `${doc.title}.${doc.docType || 'txt'}`
    const blob = new Blob([text], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = window.document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error('下载失败')
  }
}

onMounted(load)
onUnmounted(stopPoll)
</script>

<template>
  <div class="docs-page">
    <header class="docs-hero">
      <div class="docs-hero__copy">
        <nav class="docs-crumb">
          <button type="button" @click="router.push('/knowledge')">知识库管理</button>
          <span>/</span>
          <b>{{ kb?.name || '知识库' }}</b>
        </nav>
        <h1>{{ kb?.name || `知识库 ${kbId}` }}</h1>
        <p class="docs-hero__meta">文档管理 · {{ docs.length }} 篇</p>
        <p class="docs-hero__desc">
          {{ kb?.description || '为你的智能体提供知识支持，构建专业的知识库' }}
        </p>
      </div>
      <div class="docs-art" aria-hidden="true">
        <div class="docs-art__shelf" />
        <div class="docs-art__book docs-art__book--a" />
        <div class="docs-art__book docs-art__book--b" />
        <div class="docs-art__file" />
        <div class="docs-art__stack" />
      </div>
    </header>

    <div v-if="uploadPhase !== 'idle'" class="docs-upload">
      <p>
        {{ uploadPhase === 'uploading' ? '正在上传文件…' : '上传完成，正在解析入库…' }}
      </p>
      <el-progress
        :percentage="uploadPercent"
        :status="uploadPhase === 'processing' ? 'success' : undefined"
        :indeterminate="uploadPhase === 'processing'"
      />
    </div>

    <DocumentList
      :docs="docs"
      :loading="loading"
      :kb-id="kbId"
      @view="view"
      @delete="remove"
      @delete-many="removeMany"
      @reindex="reindex"
      @upload="triggerUpload"
      @download="download"
    />

    <section class="docs-foot">
      <div class="docs-foot__item">
        <i>📚</i>
        <div>
          <b>知识库</b>
          <p>构建专属知识库，支持多种文件格式</p>
        </div>
      </div>
      <div class="docs-foot__item">
        <i>◎</i>
        <div>
          <b>向量化</b>
          <p>智能分块和向量化处理</p>
        </div>
      </div>
      <div class="docs-foot__item">
        <i>✦</i>
        <div>
          <b>检索增强</b>
          <p>语义检索与混合检索</p>
        </div>
      </div>
      <div class="docs-foot__item">
        <i>💬</i>
        <div>
          <b>智能问答</b>
          <p>基于知识库的准确问答</p>
        </div>
      </div>
      <span class="docs-foot__sign">更智能的知识管理</span>
    </section>

    <el-upload
      ref="uploadRef"
      class="!hidden"
      :show-file-list="false"
      :http-request="customUpload"
      accept=".pdf,.md,.markdown,.txt"
    >
      <button />
    </el-upload>
  </div>
</template>
