'use client';

import * as React from 'react';
import * as TabsPrimitive from '@radix-ui/react-tabs';
import { cn } from '@/lib/utils';

const Tabs = TabsPrimitive.Root;

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.List>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.List
    ref={ref}
    // Underline tabs rather than a pill group. A pill's "active" state has to
    // be a lighter or darker fill, and no single fill reads as raised in both
    // themes — an underline plus a colour change is unambiguous in either.
    className={cn(
      'inline-flex h-10 w-full items-center justify-start gap-1 overflow-x-auto border-b text-muted-foreground scrollbar-thin',
      className,
    )}
    {...props}
  />
));
TabsList.displayName = TabsPrimitive.List.displayName;

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Trigger
    ref={ref}
    className={cn(
      'relative inline-flex h-10 shrink-0 items-center justify-center gap-1.5 whitespace-nowrap rounded-t-md px-3 text-sm font-medium transition-colors duration-150 ease-out hover:text-foreground disabled:pointer-events-none disabled:opacity-60',
      // The list scrolls horizontally, and an offset ring on the first or last
      // trigger gets clipped by that overflow. An inset ring with no offset
      // stays fully visible wherever the strip happens to be scrolled to.
      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring focus-visible:ring-offset-0',
      'forced-colors:focus-visible:outline forced-colors:focus-visible:outline-2',
      'after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:rounded-full after:bg-transparent after:transition-colors after:content-[""]',
      'data-[state=active]:text-primary data-[state=active]:after:bg-primary',
      // In high-contrast mode the primary underline is not painted; bold the
      // selected label so the active tab is still identifiable.
      'forced-colors:data-[state=active]:font-bold',
      className,
    )}
    {...props}
  />
));
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn(
      'mt-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
      className,
    )}
    {...props}
  />
));
TabsContent.displayName = TabsPrimitive.Content.displayName;

export { Tabs, TabsList, TabsTrigger, TabsContent };
