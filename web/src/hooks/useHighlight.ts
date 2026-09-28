import { highlight, tokenize } from '@/utils/highlight'

/** 关键词高亮 composable */
export function useHighlight() {
  function hl(text: string, query: string): string {
    return highlight(text, tokenize(query))
  }
  return { highlight: hl, tokenize }
}
