import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useUiStore = defineStore('ui', () => {
  const sidebarCollapsed = ref<boolean>(localStorage.getItem('sidebar_collapsed') === '1')
  const commandPaletteOpen = ref(false)
  const mobileSidebarOpen = ref(false)

  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
    localStorage.setItem('sidebar_collapsed', sidebarCollapsed.value ? '1' : '0')
  }

  function toggleMobileSidebar() {
    mobileSidebarOpen.value = !mobileSidebarOpen.value
  }

  function closeMobileSidebar() {
    mobileSidebarOpen.value = false
  }

  return { sidebarCollapsed, commandPaletteOpen, mobileSidebarOpen, toggleSidebar, toggleMobileSidebar, closeMobileSidebar }
})
