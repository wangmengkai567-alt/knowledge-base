<script setup lang="ts">
import { onMounted } from 'vue'
import AppHeader from './AppHeader.vue'
import AppSidebar from './AppSidebar.vue'
import { useUserStore } from '@/stores/user'

const user = useUserStore()

onMounted(() => {
  user.fetchMe()
})
</script>

<template>
  <div class="app-shell">
    <AppHeader />
    <div class="app-shell__body">
      <AppSidebar />
      <main class="app-shell__content">
        <router-view v-slot="{ Component }">
          <transition name="page" mode="out-in">
            <keep-alive include="SearchResults">
              <component :is="Component" />
            </keep-alive>
          </transition>
        </router-view>
      </main>
    </div>
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  height: 100%;
  min-height: 100vh;
  flex-direction: column;
}
.app-shell__body {
  display: flex;
  min-height: 0;
  flex: 1;
  overflow: hidden;
}
.app-shell__content {
  min-width: 0;
  flex: 1;
  overflow-y: auto;
  background: transparent;
}
.page-enter-active,
.page-leave-active {
  transition:
    opacity 220ms cubic-bezier(0.16, 1, 0.3, 1),
    transform 220ms cubic-bezier(0.16, 1, 0.3, 1);
}
.page-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.page-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
