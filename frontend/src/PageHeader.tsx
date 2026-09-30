import type { ReactNode } from 'react'

// The top of every page: underlined tabs (or just the page title) with extras on the right.
export function PageHeader<T extends string>({
  tabs,
  active,
  onTab,
  right,
}: {
  tabs: [T, string][]
  active: T
  onTab?: (tab: T) => void
  right?: ReactNode
}) {
  return (
    <header className="page-header">
      <nav className="page-tabs" role={tabs.length > 1 ? 'tablist' : undefined}>
        {tabs.map(([id, label]) =>
          tabs.length > 1 ? (
            <button key={id} role="tab" aria-selected={id === active}
                    className={id === active ? 'page-tab page-tab-active' : 'page-tab'} onClick={() => onTab?.(id)}>
              {label}
            </button>
          ) : (
            <h1 key={id} className="page-tab page-tab-active">
              {label}
            </h1>
          ),
        )}
      </nav>
      {right && <div className="page-header-right">{right}</div>}
    </header>
  )
}
