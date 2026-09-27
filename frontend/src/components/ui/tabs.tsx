import * as Tabs from '@radix-ui/react-tabs'
import { cn } from '@/lib/utils'

export const TabsRoot = Tabs.Root
export function TabsList({ className, ...props }: React.ComponentProps<typeof Tabs.List>) {
  return <Tabs.List className={cn('flex gap-1 border-b border-border', className)} {...props} />
}
export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof Tabs.Trigger>) {
  return (
    <Tabs.Trigger
      className={cn(
        '-mb-px border-b-2 border-transparent px-3 py-2 text-[13px] font-medium text-ink-muted transition-colors',
        'hover:text-ink data-[state=active]:border-accent data-[state=active]:text-ink',
        className,
      )}
      {...props}
    />
  )
}
export const TabsContent = Tabs.Content
