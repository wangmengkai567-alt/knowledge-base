<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Plus } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useKnowledgeStore } from '@/stores/knowledge'
import KnowledgeCard from '@/components/Knowledge/KnowledgeCard.vue'
import PageHeader from '@/components/Common/PageHeader.vue'
import type { KnowledgeBase, KnowledgeBasePayload } from '@/types'

const router = useRouter()
const knowledge = useKnowledgeStore()

const iconOptions = [
  { value: 'folder', label: '文件夹' },
  { value: 'document', label: '文档' },
  { value: 'cpu', label: '智能' },
  { value: 'goods', label: '收藏' },
]

const dialogVisible = ref(false)
const isEdit = ref(false)
const form = reactive<KnowledgeBasePayload & { kbId?: number }>({
  name: '',
  description: '',
  icon: 'folder',
})

function enter(kbId: number) {
  if (!kbId || kbId <= 0) return
  router.push(`/knowledge/${kbId}/documents`)
}

function openCreate() {
  isEdit.value = false
  form.kbId = undefined
  form.name = ''
  form.description = ''
  form.icon = 'folder'
  dialogVisible.value = true
}

function openEdit(kb: KnowledgeBase) {
  isEdit.value = true
  form.kbId = kb.kbId
  form.name = kb.name
  form.description = kb.description
  form.icon = kb.icon || 'folder'
  dialogVisible.value = true
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请输入知识库名称')
    return
  }
  const payload: KnowledgeBasePayload = {
    name: form.name.trim(),
    description: form.description?.trim() || undefined,
    icon: form.icon,
  }
  if (isEdit.value && form.kbId != null) {
    await knowledge.update(form.kbId, payload)
    ElMessage.success('已保存修改')
  } else {
    await knowledge.create(payload)
    ElMessage.success('知识库已创建')
  }
  dialogVisible.value = false
}

async function onDelete(kb: KnowledgeBase) {
  try {
    await ElMessageBox.confirm(
      `确认删除知识库「${kb.name}」？将同时删除其中的文档、分块和向量索引，且不可恢复。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await knowledge.remove(kb.kbId)
  ElMessage.success('已删除')
}

function onView(kb: KnowledgeBase) {
  enter(kb.kbId)
}
function onManage(kb: KnowledgeBase) {
  enter(kb.kbId)
}
function onEdit(kb: KnowledgeBase) {
  openEdit(kb)
}

knowledge.load()
</script>

<template>
  <div class="page-shell">
    <PageHeader title="知识库" :subtitle="`共 ${knowledge.kbs.length} 个知识库，点击卡片进入文档`">
      <el-button type="primary" :icon="Plus" @click="openCreate">新建知识库</el-button>
    </PageHeader>

    <div
      v-loading="knowledge.loading"
      class="stagger-in grid min-h-[160px] grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4"
    >
      <KnowledgeCard
        v-for="(kb, i) in knowledge.kbs"
        :key="kb.kbId"
        :kb="kb"
        :index="i"
        @view="onView"
        @manage="onManage"
        @edit="onEdit"
        @delete="onDelete"
      />

      <button
        type="button"
        class="group flex min-h-[220px] flex-col items-center justify-center rounded-2xl border-2 border-dashed border-ink-200/80 bg-white/40 p-5 text-ink-400 transition hover:border-brand-400 hover:bg-white/70 hover:text-brand-600"
        @click="openCreate"
      >
        <span class="flex h-11 w-11 items-center justify-center rounded-xl bg-white shadow-sm ring-1 ring-ink-100 transition group-hover:scale-105">
          <el-icon :size="22"><Plus /></el-icon>
        </span>
        <span class="mt-3 text-sm font-medium">添加知识库</span>
      </button>
    </div>

    <el-dialog v-model="dialogVisible" :title="isEdit ? '编辑知识库' : '添加知识库'" width="420px">
      <el-form label-width="84px" @submit.prevent>
        <el-form-item label="名称" required>
          <el-input
            v-model="form.name"
            maxlength="40"
            show-word-limit
            placeholder="如：产品技术文档库"
            @keyup.enter="save"
          />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="3"
            maxlength="200"
            show-word-limit
            placeholder="简要说明该知识库的用途（可选）"
          />
        </el-form-item>
        <el-form-item label="图标">
          <el-select v-model="form.icon" class="w-full">
            <el-option v-for="opt in iconOptions" :key="opt.value" :label="opt.label" :value="opt.value" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="save">{{ isEdit ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>
