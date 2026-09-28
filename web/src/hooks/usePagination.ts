import { ref, computed } from 'vue'

/** 通用分页状态管理 */
export function usePagination(defaultSize = 8) {
  const page = ref(1)
  const pageSize = ref(defaultSize)
  const total = ref(0)
  const totalPages = computed(() => Math.max(1, Math.ceil(total.value / pageSize.value)))

  function setTotal(t: number) {
    total.value = t
  }
  function setPage(p: number) {
    page.value = p
  }
  function changeSize(s: number) {
    pageSize.value = s
    page.value = 1
  }
  function reset() {
    page.value = 1
  }

  return { page, pageSize, total, totalPages, setTotal, setPage, changeSize, reset }
}
