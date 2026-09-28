<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { Search, Bell } from '@element-plus/icons-vue'
import { useUserStore } from '@/stores/user'
import { useUiStore } from '@/stores/ui'
import BrandMark from '@/components/Common/BrandMark.vue'

const router = useRouter()
const route = useRoute()
const user = useUserStore()
const ui = useUiStore()
const headerQuery = ref('')
const searchRef = ref<HTMLInputElement | null>(null)

watch(
  () => route.query.q,
  (q) => {
    if (typeof q === 'string') headerQuery.value = q
  },
  { immediate: true },
)

const displayName = computed(
  () => user.user?.nickname || user.user?.email?.split('@')[0] || '未登录',
)
const initial = computed(() => displayName.value?.trim()?.[0]?.toUpperCase() || 'U')

function onNavToggle() {
  if (window.matchMedia('(max-width: 768px)').matches) {
    ui.toggleMobileSidebar()
  } else {
    ui.toggleSidebar()
  }
}

function goHome() {
  router.push('/home')
}

function onHeaderSearch() {
  const q = headerQuery.value.trim()
  if (q) router.push({ path: '/search', query: { q } })
}

function clearHeaderSearch() {
  headerQuery.value = ''
  searchRef.value?.focus()
}

function onCommand(cmd: string) {
  if (cmd === 'logout') {
    user.logout()
    router.push('/login')
  } else if (cmd === 'settings') {
    router.push('/settings')
  } else if (cmd === 'favorites') {
    router.push('/favorites')
  }
}

function onKeydown(e: KeyboardEvent) {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
    e.preventDefault()
    searchRef.value?.focus()
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))
</script>

<template>
  <header class="app-topbar">
    <div class="app-topbar__left">
      <button
        class="icon-btn"
        type="button"
        :title="ui.sidebarCollapsed ? '展开导航' : '收起导航'"
        @click="onNavToggle"
      >
        <svg viewBox="0 0 24 24" class="h-[18px] w-[18px]" fill="none" stroke="currentColor" stroke-width="1.8">
          <path d="M4 7h16M4 12h16M4 17h16" stroke-linecap="round" />
        </svg>
      </button>
      <button class="app-topbar__brand" type="button" @click="goHome">
        <BrandMark size="sm" />
        <span class="app-topbar__brand-text">
          <b>AI 知识库</b>
          <small>让知识触手可及</small>
        </span>
      </button>
    </div>

    <div class="app-topbar__search">
      <label class="header-search">
        <el-icon class="text-ink-400"><Search /></el-icon>
        <input
          ref="searchRef"
          v-model="headerQuery"
          placeholder="搜索知识库、文档、问题…"
          @keyup.enter="onHeaderSearch"
        />
        <button
          v-if="headerQuery"
          class="header-search__clear"
          type="button"
          @click="clearHeaderSearch"
        >
          清空
        </button>
        <kbd v-else>⌘K</kbd>
      </label>
    </div>

    <div class="app-topbar__right">
      <el-popover placement="bottom-end" :width="240" trigger="click">
        <template #reference>
          <button class="icon-btn" type="button" title="通知">
            <el-icon><Bell /></el-icon>
          </button>
        </template>
        <p class="py-4 text-center text-[13px] text-ink-400">暂无新通知</p>
      </el-popover>
      <el-dropdown trigger="click" @command="onCommand">
        <div class="app-topbar__user">
          <el-avatar :size="28" class="bg-brand-600 text-[12px] font-semibold text-white ring-2 ring-white">
            {{ initial }}
          </el-avatar>
          <span class="app-topbar__name">{{ displayName }}</span>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="favorites">我的收藏</el-dropdown-item>
            <el-dropdown-item command="settings">系统设置</el-dropdown-item>
            <el-dropdown-item command="logout" divided>退出登录</el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<style scoped>
.app-topbar {
  position: relative;
  z-index: 20;
  display: flex;
  height: 60px;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 16px;
  background: rgba(255, 255, 255, 0.78);
  border-bottom: 1px solid rgba(226, 232, 240, 0.9);
  backdrop-filter: blur(18px);
}
.app-topbar__left,
.app-topbar__right {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  gap: 8px;
}
.app-topbar__brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 8px 4px 4px;
  border: 0;
  border-radius: 14px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 160ms ease;
}
.app-topbar__brand:hover {
  background: rgba(99, 102, 241, 0.08);
}
.app-topbar__brand-text {
  display: flex;
  flex-direction: column;
}
.app-topbar__brand-text b {
  font-size: 14px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: #0f172a;
  line-height: 1.2;
}
.app-topbar__brand-text small {
  font-size: 11px;
  color: #94a3b8;
  line-height: 1.2;
}
.app-topbar__search {
  display: none;
  width: min(560px, calc(100% - 420px));
  max-width: 560px;
}
.app-topbar__user {
  display: flex;
  cursor: pointer;
  align-items: center;
  gap: 8px;
  border-radius: 999px;
  padding: 4px 10px 4px 4px;
  outline: none;
  transition: background 160ms ease;
}
.app-topbar__user:hover {
  background: #fff;
}
.app-topbar__name {
  display: none;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
  color: #334155;
}
@media (min-width: 640px) {
  .app-topbar__search {
    display: block;
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
  }
}
@media (min-width: 1024px) {
  .app-topbar__name {
    display: inline;
  }
}
@media (max-width: 639px) {
  .app-topbar__brand-text {
    display: none;
  }
}
</style>
