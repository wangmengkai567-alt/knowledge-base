<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { useSettingsStore } from '@/stores/settings'
import PageHeader from '@/components/Common/PageHeader.vue'

const settings = useSettingsStore()
const config = settings.config
const tab = ref<'llm' | 'embed' | 'kb'>('llm')

function save() {
  settings.save()
  ElMessage.success('设置已保存到本机，刷新后仍有效')
}
</script>

<template>
  <div class="page-shell mx-auto max-w-5xl">
    <PageHeader title="系统设置" subtitle="偏好保存在本机。实际对话和向量化模型以服务端配置为准。" />

    <div class="liquid-card grid gap-0 overflow-hidden rounded-2xl lg:grid-cols-[200px_1fr]">
      <nav class="settings-rail space-y-1 border-b border-ink-100 p-3 lg:border-b-0 lg:border-r">
        <button type="button" :class="{ 'is-active': tab === 'llm' }" @click="tab = 'llm'">模型配置</button>
        <button type="button" :class="{ 'is-active': tab === 'embed' }" @click="tab = 'embed'">Embedding</button>
        <button type="button" :class="{ 'is-active': tab === 'kb' }" @click="tab = 'kb'">知识库</button>
      </nav>

      <div class="p-5 sm:p-6">
        <el-form v-show="tab === 'llm'" :model="config" label-width="110px" class="max-w-xl">
          <el-form-item label="LLM 模型">
            <el-input v-model="config.llmModel" />
          </el-form-item>
          <el-form-item label="温度">
            <el-slider v-model="config.temperature" :min="0" :max="1" :step="0.1" show-input />
          </el-form-item>
          <el-form-item label="最大 Token">
            <el-input-number v-model="config.maxTokens" :min="256" :max="8192" :step="256" />
          </el-form-item>
          <el-form-item label="Top P">
            <el-slider v-model="config.topP" :min="0" :max="1" :step="0.05" show-input />
          </el-form-item>
        </el-form>

        <el-form v-show="tab === 'embed'" :model="config" label-width="110px" class="max-w-xl">
          <el-form-item label="Embedding 模型">
            <el-input v-model="config.embeddingModel" />
          </el-form-item>
          <el-form-item label="向量维度">
            <el-input-number v-model="config.dimension" :min="128" :max="2048" :step="128" />
          </el-form-item>
        </el-form>

        <el-form v-show="tab === 'kb'" :model="config" label-width="110px" class="max-w-xl">
          <el-form-item label="默认分段长度">
            <el-input-number v-model="config.defaultChunkSize" :min="100" :max="2000" :step="50" />
          </el-form-item>
          <el-form-item label="默认重叠长度">
            <el-input-number v-model="config.defaultChunkOverlap" :min="0" :max="200" :step="10" />
          </el-form-item>
          <el-form-item label="上传自动索引">
            <el-switch v-model="config.autoIndex" />
          </el-form-item>
        </el-form>

        <div class="mt-6 flex justify-end border-t border-ink-100 pt-4">
          <el-button type="primary" @click="save">保存设置</el-button>
        </div>
      </div>
    </div>
  </div>
</template>
