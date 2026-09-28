import { onBeforeUnmount, onMounted, ref } from 'vue'

/** 页面级鼠标位置（-0.5 ~ 0.5），驱动视差与光斑，只写 CSS 变量。 */
export function usePagePointer() {
  const root = ref<HTMLElement | null>(null)

  function onMove(e: PointerEvent) {
    const el = root.value
    if (!el) return
    const r = el.getBoundingClientRect()
    const x = (e.clientX - r.left) / r.width - 0.5
    const y = (e.clientY - r.top) / r.height - 0.5
    el.style.setProperty('--px', x.toFixed(4))
    el.style.setProperty('--py', y.toFixed(4))
    el.style.setProperty('--mx', `${e.clientX - r.left}px`)
    el.style.setProperty('--my', `${e.clientY - r.top}px`)
  }

  onMounted(() => {
    const el = root.value
    if (!el) return
    el.style.setProperty('--px', '0')
    el.style.setProperty('--py', '0')
  })

  return { root, onMove }
}

/** 按钮磁吸：指针靠近时轻微跟随。 */
export function useMagnetic(strength = 0.22) {
  function onMove(e: PointerEvent) {
    const el = e.currentTarget as HTMLElement
    const r = el.getBoundingClientRect()
    const x = (e.clientX - r.left - r.width / 2) * strength
    const y = (e.clientY - r.top - r.height / 2) * strength
    el.style.transform = `translate(${x}px, ${y}px)`
  }
  function onLeave(e: PointerEvent) {
    const el = e.currentTarget as HTMLElement
    el.style.transform = ''
  }
  return { onMove, onLeave }
}

export function useCursorFollow() {
  const pos = ref({ x: 0, y: 0, on: false })
  function move(e: PointerEvent) {
    pos.value = { x: e.clientX, y: e.clientY, on: true }
  }
  function leave() {
    pos.value = { ...pos.value, on: false }
  }
  onMounted(() => {
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerleave', leave)
  })
  onBeforeUnmount(() => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerleave', leave)
  })
  return pos
}
