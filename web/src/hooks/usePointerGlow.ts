/** 把指针位置写到 CSS 变量，供 Spotlight / 轻微倾斜使用（仅自定义属性，不触发 layout）。 */
export function usePointerGlow() {
  function onMove(e: PointerEvent) {
    const node = e.currentTarget as HTMLElement | null
    if (!node) return
    const r = node.getBoundingClientRect()
    if (!r.width || !r.height) return
    const x = (e.clientX - r.left) / r.width
    const y = (e.clientY - r.top) / r.height
    node.style.setProperty('--mx', `${x * 100}%`)
    node.style.setProperty('--my', `${y * 100}%`)
    node.style.setProperty('--rx', `${(0.5 - y) * 5}deg`)
    node.style.setProperty('--ry', `${(x - 0.5) * 6}deg`)
  }

  function onLeave(e: PointerEvent) {
    const node = e.currentTarget as HTMLElement | null
    if (!node) return
    node.style.setProperty('--mx', '50%')
    node.style.setProperty('--my', '0%')
    node.style.setProperty('--rx', '0deg')
    node.style.setProperty('--ry', '0deg')
  }

  return { onMove, onLeave }
}
