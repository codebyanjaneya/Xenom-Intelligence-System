import { useState } from 'react'
import {
  type ColumnDef, type SortingState, flexRender, getCoreRowModel, getSortedRowModel,
  getFilteredRowModel, useReactTable,
} from '@tanstack/react-table'
import { ArrowDown, ArrowUp, ArrowUpDown, Search } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Input } from './input'
import { EmptyState } from './states'

export function DataTable<T>({
  columns, data, searchPlaceholder, emptyText, onRowClick, defaultSort,
}: {
  columns: ColumnDef<T, any>[]
  data: T[]
  searchPlaceholder?: string
  emptyText: string
  onRowClick?: (row: T) => void
  defaultSort?: SortingState
}) {
  const [sorting, setSorting] = useState<SortingState>(defaultSort ?? [])
  const [globalFilter, setGlobalFilter] = useState('')
  const table = useReactTable({
    data, columns, state: { sorting, globalFilter }, onSortingChange: setSorting, onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(), getFilteredRowModel: getFilteredRowModel(),
  })

  return (
    <div>
      {searchPlaceholder && (
        <div className="relative mb-3 max-w-xs">
          <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-faint" />
          <Input value={globalFilter} onChange={e => setGlobalFilter(e.target.value)} placeholder={searchPlaceholder} className="pl-8" />
        </div>
      )}
      {data.length === 0 ? <EmptyState text={emptyText} /> : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full min-w-max border-collapse text-[13px]">
            <thead>
              {table.getHeaderGroups().map(hg => (
                <tr key={hg.id} className="border-b border-border bg-surface-2">
                  {hg.headers.map(h => (
                    <th key={h.id} className="whitespace-nowrap px-3 py-2 text-left font-medium text-ink-muted">
                      {h.isPlaceholder ? null : (
                        <button
                          className={cn('inline-flex items-center gap-1', h.column.getCanSort() && 'cursor-pointer select-none hover:text-ink')}
                          onClick={h.column.getToggleSortingHandler()}
                          disabled={!h.column.getCanSort()}
                        >
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {h.column.getCanSort() && (
                            h.column.getIsSorted() === 'asc' ? <ArrowUp size={12} /> : h.column.getIsSorted() === 'desc' ? <ArrowDown size={12} /> : <ArrowUpDown size={12} className="opacity-40" />
                          )}
                        </button>
                      )}
                    </th>
                  ))}
                </tr>
              ))}
            </thead>
            <tbody>
              {table.getRowModel().rows.map(row => (
                <tr
                  key={row.id}
                  onClick={() => onRowClick?.(row.original)}
                  className={cn('border-b border-border last:border-0', onRowClick && 'cursor-pointer hover:bg-surface-2')}
                >
                  {row.getVisibleCells().map(cell => (
                    <td key={cell.id} className="px-3 py-2.5 align-top text-ink">{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
