<script setup lang="ts">
import { computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useUiStore } from '@/stores/ui'
import { HomeFilled, Collection, Clock, Star, Setting } from '@element-plus/icons-vue'
import { usePointerGlow } from '@/hooks/usePointerGlow'
import BrandMark from '@/components/Common/BrandMark.vue'
import type { Component } from 'vue'

const ui = useUiStore()
const route = useRoute()
const { onMove, onLeave } = usePointerGlow()

watch(
  () => route.path,
  () => ui.closeMobileSidebar(),
)

interface MenuItem {
  path: string
  title: string
  icon: Component
}
const menus: MenuItem[] = [
  { path: '/home', title: '首页', icon: HomeFilled },
  { path: '/knowledge', title: '知识库管理', icon: Collection },
  { path: '/history', title: '检索历史', icon: Clock },
  { path: '/favorites', title: '我的收藏', icon: Star },
  { path: '/settings', title: '系统设置', icon: Setting },
]

const collapsed = computed(() => ui.sidebarCollapsed && !ui.mobileSidebarOpen)

function isActive(path: string) {
  if (path === '/home') return route.path === '/home' || route.path === '/search'
  return route.path === path || route.path.startsWith(`${path}/`)
}
</script>

<template>
  <aside
    class="app-rail"
    :class="{ 'is-collapsed': collapsed, 'mobile-sidebar-open': ui.mobileSidebarOpen }"
  >
    <nav class="app-rail__nav" aria-label="主导航">
      <router-link
        v-for="m in menus"
        :key="m.path"
        :to="m.path"
        class="rail-link"
        :class="{ 'is-active': isActive(m.path) }"
        :title="collapsed ? m.title : undefined"
        @pointermove="onMove"
        @pointerleave="onLeave"
      >
        <el-icon><component :is="m.icon" /></el-icon>
        <span v-show="!collapsed">{{ m.title }}</span>
      </router-link>
    </nav>

    <div
      v-if="!collapsed"
      class="app-rail__promo"
      @pointermove="onMove"
      @pointerleave="onLeave"
    >
      <BrandMark size="sm" />
      <div>
        <p>AI 知识库助手</p>
        <span>基于大模型的智能知识问答系统</span>
      </div>
    </div>
    <p v-if="!collapsed" class="app-rail__copy">© 2025 AI 知识库</p>
  </aside>
  <button
    v-if="ui.mobileSidebarOpen"
    class="mobile-sidebar-backdrop"
    aria-label="关闭导航"
    type="button"
    @click="ui.closeMobileSidebar()"
  />
</template>

<style scoped>
.app-rail {
  --mx: 50%;
  --my: 40%;
  position: relative;
  display: flex;
  flex-direction: column;
  width: 232px;
  flex-shrink: 0;
  height: 100%;
  padding: 14px 14px 16px;
  overflow: hidden;
  background: transparent;
  transition: width 220ms cubic-bezier(0.16, 1, 0.3, 1), padding 220ms cubic-bezier(0.16, 1, 0.3, 1);
}
.app-rail.is-collapsed {
  width: 76px;
  padding-inline: 12px;
  align-items: center;
}
.app-rail__nav {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 6px;
}
.rail-link {
  position: relative;
  display: flex;
  align-items: center;
  gap: 12px;
  height: 44px;
  padding: 0 14px;
  overflow: hidden;
  border-radius: 14px;
  color: #64748b;
  text-decoration: none;
  font-size: 14px;
  font-weight: 500;
  isolation: isolate;
  transition:
    color 160ms ease,
    background 160ms ease,
    transform 160ms ease,
    box-shadow 160ms ease;
}
.rail-link::after {
  content: '';
  position: absolute;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  background: radial-gradient(120px circle at var(--mx) var(--my), rgba(255, 255, 255, 0.85), transparent 55%);
  opacity: 0;
  transition: opacity 160ms ease;
}
.rail-link > * {
  position: relative;
  z-index: 1;
}
.rail-link .el-icon {
  font-size: 18px;
  transition: transform 160ms ease;
}
.rail-link:hover {
  color: #4338ca;
  background: rgba(255, 255, 255, 0.72);
  transform: translateX(2px);
}
.rail-link:hover::after {
  opacity: 1;
}
.rail-link:hover .el-icon {
  transform: scale(1.08);
}
.rail-link.is-active {
  color: #4f46e5;
  font-weight: 650;
  background: linear-gradient(90deg, #eef2ff, #e4e9ff 88%);
  box-shadow: 0 8px 18px rgba(79, 70, 229, 0.1);
}
.rail-link.is-active:hover {
  transform: none;
}
.app-rail.is-collapsed .rail-link {
  width: 44px;
  padding: 0;
  justify-content: center;
}
.app-rail__promo {
  --mx: 70%;
  --my: 80%;
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  overflow: hidden;
  margin-top: 12px;
  padding: 14px;
  border: 1px solid rgba(199, 210, 254, 0.7);
  border-radius: 18px;
  color: #312e81;
  background:
    radial-gradient(120px circle at 92% 120%, rgba(99, 102, 241, 0.16), transparent 52%),
    linear-gradient(180deg, #eef2ff 0%, #f8fafc 100%);
  box-shadow: 0 8px 20px rgba(79, 70, 229, 0.08);
  cursor: default;
  transition: transform 180ms ease, box-shadow 180ms ease;
}
.app-rail__promo::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: radial-gradient(140px circle at var(--mx) var(--my), rgba(255, 255, 255, 0.55), transparent 58%);
  opacity: 0;
  transition: opacity 180ms ease;
}
.app-rail__promo::after {
  display: none;
}
.app-rail__promo:hover {
  transform: translateY(-2px);
  box-shadow: 0 14px 28px rgba(79, 70, 229, 0.14);
}
.app-rail__promo:hover::before {
  opacity: 1;
}
.app-rail__promo p,
.app-rail__promo span {
  position: relative;
  z-index: 1;
}
.app-rail__promo p {
  display: block;
  margin: 0;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.01em;
  color: #1e1b4b;
}
.app-rail__promo span {
  display: block;
  margin-top: 4px;
  font-size: 11px;
  line-height: 1.5;
  color: #64748b;
}
.app-rail__copy {
  margin: 12px 4px 0;
  font-size: 11px;
  color: #94a3b8;
}
</style>
