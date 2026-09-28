<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { Document, Files } from '@element-plus/icons-vue'
import { formatFileSize, formatDate } from '@/utils/format'
import type { DocDetail } from '@/types'

const props = defineProps<{
  doc: DocDetail | null
  loading?: boolean
  /** 已渲染好的 Markdown HTML（由父组件基于完整文档生成） */
  html?: string
}>()

// 渲染方式：markdown（默认，应用内渲染预览）/ html（全屏 Dialog 以独立网页形式渲染）
const viewMode = ref<'markdown' | 'html'>('markdown')

// HTML 渲染用全屏 Dialog 承载 iframe，避免被挤在中间列里显得窗口太小
const htmlDialog = ref(false)
watch(viewMode, (v) => {
  htmlDialog.value = v === 'html'
})
function onHtmlDialogUpdate(v: boolean) {
  htmlDialog.value = v
  if (!v) viewMode.value = 'markdown'
}

// 把渲染后的 HTML 包成一个完整独立网页，供 iframe 像普通网页一样展示。
// 内置 no-referrer 以绕过外链图床防盗链；sandbox 屏蔽脚本，双重防 XSS。
const htmlPage = computed(() => {
  const body = props.html ?? ''
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="referrer" content="no-referrer">
<style>
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"PingFang SC","Microsoft YaHei",sans-serif;line-height:1.75;color:#1f2937;background:#fff;margin:0;padding:32px 48px;}
  .markdown-body{max-width:1280px;margin:0 auto;}
  h1,h2,h3,h4{line-height:1.3;margin:1.4em 0 .6em;font-weight:600;}
  h1{font-size:1.8em;} h2{font-size:1.5em;} h3{font-size:1.25em;}
  p{margin:.8em 0;}
  img{max-width:100%;height:auto;border-radius:6px;margin:.6em 0;}
  a{color:#2563eb;}
  pre{background:#f6f8fa;padding:14px 16px;border-radius:8px;overflow:auto;}
  code{background:#f6f8fa;padding:2px 5px;border-radius:4px;font-size:.9em;}
  pre code{background:none;padding:0;}
  blockquote{border-left:4px solid #e5e7eb;margin:1em 0;padding:.2em 1em;color:#4b5563;background:#f9fafb;}
  table{border-collapse:collapse;margin:1em 0;width:100%;}
  th,td{border:1px solid #e5e7eb;padding:8px 12px;text-align:left;}
  ul,ol{padding-left:1.6em;}
</style>
</head>
<body><div class="markdown-body">${body}</div></body>
</html>`
})
</script>

<template>
  <div class="flex h-full flex-col liquid-card rounded-2xl p-5">
    <div v-if="doc" class="flex min-h-0 flex-1 flex-col">
      <!-- 头部：标题与文件信息 -->
      <div class="flex items-start gap-3">
        <div class="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
          <el-icon :size="20"><Document /></el-icon>
        </div>
        <div class="min-w-0">
          <h3 class="truncate text-base font-semibold text-ink-900">{{ doc.title }}</h3>
          <p class="mt-0.5 text-xs text-ink-400">
            {{ doc.file?.originalName }} · {{ formatFileSize(doc.file?.fileSize || 0) }}
          </p>
        </div>
      </div>

      <el-descriptions class="mt-4" :column="2" size="small" border>
        <el-descriptions-item label="文档类型">{{ doc.docTypeDesc }}</el-descriptions-item>
        <el-descriptions-item label="字符数">{{ formatFileSize(doc.charCount) }}</el-descriptions-item>
        <el-descriptions-item label="分段数">{{ doc.chunkCount }}</el-descriptions-item>
        <el-descriptions-item label="上传时间">{{ formatDate(doc.createdAt, true) }}</el-descriptions-item>
        <el-descriptions-item label="索引时间">{{ formatDate(doc.indexedAt, true) }}</el-descriptions-item>
      </el-descriptions>

      <!-- 文档内容（Markdown 渲染预览 / 或查看生成的 HTML 源码） -->
      <div class="mt-4 flex min-h-0 flex-1 flex-col">
        <div class="mb-2 flex items-center justify-between gap-2">
          <div class="flex items-center gap-2">
            <el-icon class="text-ink-400"><Files /></el-icon>
            <span class="text-sm font-semibold text-ink-700">文档内容</span>
          </div>
          <el-radio-group v-model="viewMode" size="small">
            <el-radio-button value="markdown">Markdown 渲染</el-radio-button>
            <el-radio-button value="html">HTML 渲染</el-radio-button>
          </el-radio-group>
        </div>

        <div class="min-h-0 flex-1 overflow-y-auto rounded-lg border border-ink-100 bg-ink-50/40 p-4">
          <div v-if="viewMode === 'markdown'">
            <div v-if="html" class="markdown-body" v-html="html"></div>
            <p v-else class="py-8 text-center text-xs text-ink-300">
              暂无解析内容，请稍后或重新解析该文档。
            </p>
          </div>
          <div
            v-else
            class="flex h-full flex-col items-center justify-center gap-2 text-center text-xs text-ink-400"
          >
            <p>HTML 渲染已在全屏窗口打开（更接近真实网页效果）。</p>
            <el-button size="small" text type="primary" @click="htmlDialog = true">重新打开窗口</el-button>
          </div>
        </div>

        <p class="mt-2 text-[11px] leading-relaxed text-ink-300">
          以上为按原始 Markdown 渲染的预览；若原文件包含表格、图片等富格式，完整版式请在知识库源文件中查看。切换到「HTML 渲染」会以全屏独立网页形式展示（iframe），更接近真实页面效果。
        </p>
      </div>

      <!-- HTML 渲染：全屏 Dialog，iframe 铺满整屏，避免被中间列挤窄 -->
      <el-dialog
        :model-value="htmlDialog"
        title="HTML 渲染预览（独立网页）"
        append-to-body
        align-center
        :fullscreen="false"
        width="96vw"
        class="html-render-dialog"
        @update:model-value="onHtmlDialogUpdate"
      >
        <iframe
          :srcdoc="htmlPage"
          referrerpolicy="no-referrer"
          sandbox=""
          class="html-render-frame border-0 bg-white"
        ></iframe>
      </el-dialog>
    </div>

    <div v-else-if="loading" class="space-y-3">
      <div class="skeleton h-10 w-full" />
      <div class="skeleton h-20 w-full" />
      <div class="skeleton h-40 w-full" />
    </div>
  </div>
</template>

<!-- 全屏 Dialog 用全局样式（Dialog 默认 append-to-body，scoped 选择器对其失效）。
     靠自定义 class 唯一定位，不污染其它组件。 -->
<style>
.html-render-dialog.el-dialog {
  width: 96vw !important;
  max-width: 96vw !important;
  height: 92vh !important;
  margin: 0 auto !important;
  display: flex;
  flex-direction: column;
}
.html-render-dialog .el-dialog__header {
  margin-right: 0;
  padding: 12px 20px;
  border-bottom: 1px solid #eef0f3;
}
.html-render-dialog .el-dialog__body {
  padding: 0;
  flex: 1;
  min-height: 0;
  overflow: hidden;
}
.html-render-frame {
  display: block;
  width: 100%;
  height: 100%;
  min-height: calc(92vh - 56px);
}
</style>
