import { useEffect, useRef, useState } from 'react'
import { Layers } from 'lucide-react'
import { loadMetadataFilters } from '../api'
import { sourceLabel } from '../lib/sourceLabels'

/**
 * 搜索集（search set）：**检索之前**圈定数据来源。
 *
 * 与页面上原有的「Data source」筛选是两层东西 —— 那个筛的是**已经取回来**的行，
 * 这个定的是**去取哪些行**。两层都保留：检索范围大的时候，显示筛选自然还有用。
 * 所以这个控件不替换任何现有筛选器，它是新加的一层。
 *
 * 空数组 = 全部（沿用全仓库「空数组即不过滤」的约定，没有 `all` 哨兵值）。
 * 会话内跨页保持：状态在 `App`，不给 localStorage —— 刷新浏览器回到「全部」。
 *
 * 它同时是用户要的那个「常驻显示当前搜索集的小模块」：一个控件既显示当前值、
 * 点开又能改。不做两个（那会出现两个控件说同一件事）。
 */

export type SearchSetOption = { value: string; count: number }

/**
 * `/metadata/filter-options` 每个页面各取一次是浪费 —— 三个页面都会挂这个控件。
 * 一个会话内取一次就够（搜索集候选只随 ETL 变化）。失败时不缓存，下次挂载重试。
 */
let optionsPromise: Promise<SearchSetOption[]> | null = null

function loadSearchSetOptions(): Promise<SearchSetOption[]> {
  if (!optionsPromise) {
    optionsPromise = loadMetadataFilters()
      .then((payload) => payload.searchSets ?? [])
      .catch(() => {
        optionsPromise = null
        return []
      })
  }
  return optionsPromise
}

export function SearchSetControl({
  value,
  onChange,
}: {
  /** 当前搜索集，`[]` = 全部。 */
  value: string[]
  onChange: (next: string[]) => void
}) {
  const [options, setOptions] = useState<SearchSetOption[]>([])
  const detailsRef = useRef<HTMLDetailsElement | null>(null)

  useEffect(() => {
    let cancelled = false
    loadSearchSetOptions().then((next) => {
      if (!cancelled) setOptions(next)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const total = options.reduce((sum, option) => sum + option.count, 0)
  const current = value[0]
  const currentLabel = current ? sourceLabel(current) : 'All sources'
  const currentCount = current ? options.find((option) => option.value === current)?.count : total

  // 只列**真的有行**的来源（见后端 `searchSets` 的说明）：选中一个 0 行的集合
  // 就是一次空检索，而用户看不出那是自己选的。
  const choices: Array<{ key: string; label: string; count?: number; next: string[] }> = [
    { key: '', label: 'All sources', count: total || undefined, next: [] },
    ...options.map((option) => ({
      key: option.value,
      label: sourceLabel(option.value),
      count: option.count,
      next: [option.value],
    })),
  ]

  const pick = (next: string[]) => {
    // 原生 <details>（与页面上其它筛选下拉一致），选完手动收起。
    if (detailsRef.current) detailsRef.current.open = false
    if (next[0] !== current || next.length !== value.length) onChange(next)
  }

  return (
    <details className="search-table-filter-dropdown search-set-dropdown" ref={detailsRef}>
      <summary
        title={
          current
            ? `Searching only ${currentLabel} — ${currentCount?.toLocaleString() ?? '?'} entries`
            : `Searching every source — ${total.toLocaleString()} entries`
        }
      >
        <Layers size={13} />
        <span>Search set</span>
        <small className="search-set-current">
          {currentLabel}
          {currentCount ? ` (${currentCount.toLocaleString()})` : ''}
        </small>
      </summary>

      <div className="search-table-filter-popover search-set-popover">
        <p className="search-set-title">Search set</p>
        <p className="search-set-hint">Limits where every search looks. Downloads are not affected.</p>
        <div className="search-set-row">
          {choices.map((choice) => {
            const active = choice.key === (current ?? '')
            return (
              <button
                key={choice.key || 'all'}
                type="button"
                className={`search-set-option ${active ? 'on' : ''}`}
                onClick={() => pick(choice.next)}
                aria-pressed={active}
              >
                <span>{choice.label}</span>
                {choice.count !== undefined && <small>{choice.count.toLocaleString()}</small>}
              </button>
            )
          })}
        </div>
      </div>
    </details>
  )
}
