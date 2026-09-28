<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Message, Lock, User, ChatDotRound } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { ElMessage } from 'element-plus'
import BrandMark from '@/components/Common/BrandMark.vue'
import GlassStack from '@/components/Visual/GlassStack.vue'
import { usePagePointer, useMagnetic } from '@/hooks/useMouseMotion'

const user = useUserStore()
const route = useRoute()
const router = useRouter()
const { root, onMove } = usePagePointer()
const mag = useMagnetic(0.18)

const REMEMBER_KEY = 'ai_kb_remember_email'
const form = reactive({
  email: localStorage.getItem(REMEMBER_KEY) || 'admin@company.com',
  password: 'Admin@1234',
  remember: !!localStorage.getItem(REMEMBER_KEY),
})
const loading = ref(false)
const tab = ref<'account' | 'otp'>('account')

onMounted(() => {
  if (!localStorage.getItem(REMEMBER_KEY)) {
    form.email = 'admin@company.com'
  }
})

async function onSubmit() {
  if (tab.value === 'otp') {
    ElMessage.info('验证码登录即将开放，请使用账号登录')
    return
  }
  if (!form.email || !form.password) {
    ElMessage.warning('请输入邮箱与密码')
    return
  }
  loading.value = true
  try {
    await user.login(form.email, form.password)
    if (form.remember) localStorage.setItem(REMEMBER_KEY, form.email)
    else localStorage.removeItem(REMEMBER_KEY)
    ElMessage.success('登录成功')
    router.push((route.query.redirect as string) || '/home')
  } catch {
    /* 错误已在拦截器提示 */
  } finally {
    loading.value = false
  }
}

function soon() {
  ElMessage.info('该能力即将开放')
}
</script>

<template>
  <div ref="root" class="login-page" @pointermove="onMove">
    <header class="login-top">
      <div class="flex items-center gap-2.5">
        <BrandMark size="sm" />
        <div>
          <p class="text-[14px] font-semibold tracking-tight text-ink-900">AI 知识库</p>
          <p class="text-[11px] text-ink-400">让知识触手可及</p>
        </div>
      </div>
      <p class="hidden text-[12px] tracking-[0.18em] text-ink-400 sm:block">探索 · 连接 · 创造更大的价值</p>
    </header>

    <div class="login-grid">
      <section class="login-copy">
        <h1 class="login-copy__title">
          AI 驱动的<span>知识管理平台</span>
        </h1>
        <p class="login-copy__desc">
          整合多源知识，构建专属知识库，让信息检索更智能、让知识管理更简单。
        </p>
        <ul class="login-feats">
          <li>
            <span class="login-feats__icon">📄</span>
            <div>
              <b>多源接入</b>
              <p>支持多种文档格式</p>
            </div>
          </li>
          <li>
            <span class="login-feats__icon">🔍</span>
            <div>
              <b>智能检索</b>
              <p>语义理解，精准匹配</p>
            </div>
          </li>
          <li>
            <span class="login-feats__icon">✦</span>
            <div>
              <b>AI 助手</b>
              <p>知识问答，深度思考</p>
            </div>
          </li>
        </ul>
        <GlassStack class="mt-6 hidden lg:block" />
      </section>

      <form class="login-card" @submit.prevent="onSubmit">
        <div class="mb-6 flex flex-col items-center text-center">
          <BrandMark size="lg" />
          <h2 class="mt-3 text-[1.25rem] font-semibold tracking-tight text-ink-900">AI 知识库</h2>
          <p class="mt-1 text-[13px] text-ink-400">欢迎回来，请登录您的账号</p>
        </div>

        <div class="login-tabs">
          <button type="button" :class="{ 'is-on': tab === 'account' }" @click="tab = 'account'">账号登录</button>
          <button type="button" :class="{ 'is-on': tab === 'otp' }" @click="tab = 'otp'">验证码登录</button>
        </div>

        <template v-if="tab === 'account'">
          <label class="login-field">
            <el-icon><User /></el-icon>
            <input v-model="form.email" type="text" placeholder="请输入邮箱或用户名" autocomplete="username" />
          </label>
          <label class="login-field">
            <el-icon><Lock /></el-icon>
            <input
              v-model="form.password"
              type="password"
              placeholder="请输入密码"
              autocomplete="current-password"
              @keyup.enter="onSubmit"
            />
          </label>
          <div class="mb-4 flex items-center justify-between text-[12px] text-ink-500">
            <label class="inline-flex cursor-pointer items-center gap-1.5">
              <input v-model="form.remember" type="checkbox" class="accent-brand-600" />
              记住账号
            </label>
            <button type="button" class="text-brand-600 hover:underline" @click="soon">
              忘记密码?
            </button>
          </div>
        </template>
        <template v-else>
          <p class="mb-4 rounded-xl bg-brand-50 px-3 py-3 text-center text-[13px] text-brand-700">
            验证码登录即将开放，请使用账号登录。
          </p>
        </template>

        <button
          class="login-submit btn-shine"
          type="submit"
          :disabled="loading"
          @pointermove="mag.onMove"
          @pointerleave="mag.onLeave"
        >
          {{ loading ? '登录中…' : '登录 →' }}
        </button>

        <p class="mt-5 text-center text-[11px] tracking-wide text-ink-400">其他登录方式</p>
        <div class="mt-3 flex justify-center gap-3">
          <button class="login-social" type="button" title="GitHub" @click="soon">
            <svg viewBox="0 0 24 24" class="h-4 w-4" fill="currentColor">
              <path
                d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.46-1.16-1.12-1.47-1.12-1.47-.92-.63.07-.62.07-.62 1 .07 1.53 1.04 1.53 1.04.9 1.55 2.36 1.1 2.94.84.09-.65.35-1.1.64-1.35-2.22-.25-4.56-1.11-4.56-4.95 0-1.1.39-1.99 1.03-2.69-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.02A9.56 9.56 0 0 1 12 6.84c.85 0 1.7.11 2.5.33 1.9-1.29 2.74-1.02 2.74-1.02.55 1.38.2 2.4.1 2.65.64.7 1.03 1.6 1.03 2.69 0 3.85-2.34 4.7-4.57 4.95.36.31.68.92.68 1.86v2.76c0 .26.18.58.69.48A10 10 0 0 0 12 2Z"
              />
            </svg>
          </button>
          <button class="login-social" type="button" title="微信" @click="soon">
            <el-icon><ChatDotRound /></el-icon>
          </button>
          <button class="login-social" type="button" title="邮箱" @click="soon">
            <el-icon><Message /></el-icon>
          </button>
        </div>
        <p class="mt-5 text-center text-[12px] text-ink-400">
          还没有账号？
          <button type="button" class="font-medium text-brand-600 hover:underline" @click="soon">
            立即注册
          </button>
        </p>
      </form>
    </div>
  </div>
</template>
