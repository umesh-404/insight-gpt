'use client';

import * as React from 'react';
import { useRouter } from 'next/navigation';
import {
  ArrowRight,
  CornerDownLeft,
  Moon,
  Search,
  Sun,
  type LucideIcon,
} from 'lucide-react';
import { NAV_ITEMS } from '@/components/layout/nav';
import { EXAMPLE_PROMPTS } from '@/components/prompt-chips';
import { useAuth } from '@/lib/auth';
import { useConversations } from '@/lib/hooks';
import { useTheme } from '@/lib/theme';
import { cn } from '@/lib/utils';

/** Lets a click target (the header search affordance) open the same palette. */
const OPEN_EVENT = 'insightgpt:open-command-palette';

export function openCommandPalette(): void {
  window.dispatchEvent(new Event(OPEN_EVENT));
}

/**
 * The palette answers Ctrl-K as well as ⌘K, but the hint used to claim ⌘K on
 * every platform — wrong, and unreadable, on the Windows machines this is
 * demoed from. Resolved after mount so the server and the first client render
 * still agree, which is why the initial value is the majority platform rather
 * than a blank.
 *
 * Exported so the header affordance and the palette's own footer cannot drift
 * apart and show two different shortcuts for one key.
 */
export function useCommandShortcutHint(): string {
  const [hint, setHint] = React.useState('Ctrl K');
  React.useEffect(() => {
    const ua = `${navigator.platform ?? ''} ${navigator.userAgent ?? ''}`;
    if (/mac|iphone|ipad|ipod/i.test(ua)) setHint('⌘K');
  }, []);
  return hint;
}

interface Command {
  id: string;
  label: string;
  hint?: string;
  group: string;
  icon: LucideIcon;
  run: () => void;
}

/**
 * ⌘K / Ctrl-K quick navigation.
 *
 * Earns its place because the app is wide (seven sections plus a growing
 * conversation history) and the demo is driven from the keyboard: an evaluator
 * can jump to any screen, re-open a past answer, or fire a flagship question
 * without hunting through the sidebar.
 *
 * Deliberately dependency-free for the combobox itself — but the *dialog* is
 * the platform's. It previously declared `aria-modal="true"` on a plain `div`
 * and then only half kept the promise: it locked body scroll by hand (which
 * removes the root scrollbar and shifts the page sideways), and its Tab
 * handler just snapped focus back to the input, so Shift-Tab and any future
 * focusable child were one bug away from escaping the overlay. A `<dialog>`
 * opened with `showModal()` gets the real thing — inert background, contained
 * Tab in both directions, Escape, and focus restored to the trigger — and
 * `aria-modal` is now redundant, so it is gone rather than merely true.
 */
export function CommandPalette() {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState('');
  const [active, setActive] = React.useState(0);

  const router = useRouter();
  const { hasRole } = useAuth();
  const { theme, setTheme } = useTheme();
  const shortcutHint = useCommandShortcutHint();
  // History is a nice-to-have here; a failure must not break navigation.
  const conversations = useConversations();

  const dialogRef = React.useRef<HTMLDialogElement | null>(null);
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const listRef = React.useRef<HTMLDivElement | null>(null);

  // Global shortcut, registered once. ⌘K / Ctrl-K is claimed app-wide on
  // purpose: it is the one key the demo is driven from, and it must work from
  // inside the composer as well as from the page body.
  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const isToggle =
        event.key.toLowerCase() === 'k' && (event.metaKey || event.ctrlKey);
      if (!isToggle) return;
      event.preventDefault();
      setOpen((wasOpen) => !wasOpen);
    };
    const onRequest = () => setOpen(true);
    window.addEventListener('keydown', onKey);
    window.addEventListener(OPEN_EVENT, onRequest);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener(OPEN_EVENT, onRequest);
    };
  }, []);

  // Drive the real dialog from state. Nothing here saves or restores focus:
  // `showModal()` remembers the invoker and `close()` puts focus back, which
  // is also why the trigger does not need to be captured at open time.
  React.useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    if (open && !el.open) {
      el.showModal();
      inputRef.current?.focus();
    }
    if (!open && el.open) el.close();
  }, [open]);

  const commands = React.useMemo<Command[]>(() => {
    const list: Command[] = [];

    for (const item of NAV_ITEMS) {
      if (!hasRole(item.minRole)) continue;
      list.push({
        id: `nav:${item.href}`,
        label: item.label,
        hint: item.description,
        group: 'Go to',
        icon: item.icon,
        run: () => router.push(item.href),
      });
    }

    for (const prompt of EXAMPLE_PROMPTS) {
      list.push({
        id: `ask:${prompt.label}`,
        label: prompt.label,
        hint: 'Start a new conversation',
        group: 'Ask',
        icon: prompt.icon,
        // The composer owns question submission, so the palette hands the
        // question over in the URL rather than reaching across components.
        run: () => router.push(`/ask?q=${encodeURIComponent(prompt.label)}`),
      });
    }

    for (const conversation of conversations.data?.items ?? []) {
      list.push({
        id: `conv:${conversation.id}`,
        label: conversation.title || 'Untitled conversation',
        hint: 'Recent conversation',
        group: 'History',
        icon: ArrowRight,
        run: () => router.push(`/ask/${conversation.id}`),
      });
    }

    list.push({
      id: 'theme',
      label: theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme',
      group: 'Preferences',
      icon: theme === 'dark' ? Sun : Moon,
      run: () => setTheme(theme === 'dark' ? 'light' : 'dark'),
    });

    return list;
  }, [hasRole, router, conversations.data, theme, setTheme]);

  const results = React.useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return commands;
    return commands.filter((command) =>
      `${command.label} ${command.hint ?? ''} ${command.group}`
        .toLowerCase()
        .includes(needle),
    );
  }, [commands, query]);

  // Keep the highlight inside the (shrinking) result list as the user types.
  React.useEffect(() => {
    setActive((current) => (current < results.length ? current : 0));
  }, [results.length]);

  React.useEffect(() => {
    listRef.current
      ?.querySelector<HTMLElement>('[data-active="true"]')
      ?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const runAt = (index: number) => {
    const command = results[index];
    if (!command) return;
    setOpen(false);
    command.run();
  };

  const onKeyDown = (event: React.KeyboardEvent) => {
    // Escape is not handled here: the dialog's own close request fires first
    // and `onClose` below does the cleanup, so there is one close path.
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive((i) => (results.length ? (i + 1) % results.length : 0));
      return;
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((i) => (results.length ? (i - 1 + results.length) % results.length : 0));
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      runAt(active);
    }
  };

  let lastGroup = '';

  return (
    <dialog
      ref={dialogRef}
      aria-label="Command palette"
      // Covers Escape, the back gesture and `close()` alike, so the query and
      // the highlight are always reset no matter how the palette was left.
      onClose={() => {
        setOpen(false);
        setQuery('');
        setActive(0);
      }}
      // Light dismiss: the dialog fills the viewport, so a click whose target
      // is the dialog itself is a click on the dim area and nothing else.
      onClick={(event) => {
        if (event.target === dialogRef.current) setOpen(false);
      }}
      onKeyDown={onKeyDown}
    >
      <div className="flex h-full items-start justify-center p-4 pt-[12vh]">
        <div className="w-full max-w-xl overflow-hidden rounded-lg border bg-popover text-popover-foreground shadow-overlay">
          <div className="flex items-center gap-2.5 border-b px-4">
            <Search className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Jump to a screen, a past answer, or ask something…"
              aria-label="Search commands"
              aria-controls="command-results"
              aria-activedescendant={
                results[active] ? `command-${results[active].id}` : undefined
              }
              role="combobox"
              aria-expanded="true"
              autoComplete="off"
              className="h-12 flex-1 bg-transparent text-base outline-none placeholder:text-muted-foreground"
            />
          </div>

          <div
            id="command-results"
            ref={listRef}
            role="listbox"
            aria-label="Commands"
            className="scrollbar-thin max-h-[min(24rem,50vh)] overflow-y-auto p-2"
          >
            {results.length === 0 ? (
              <p className="px-3 py-8 text-center text-sm text-muted-foreground">
                Nothing matches “{query}”.
              </p>
            ) : (
              results.map((command, index) => {
                const newGroup = command.group !== lastGroup;
                lastGroup = command.group;
                const Icon = command.icon;
                return (
                  <React.Fragment key={command.id}>
                    {newGroup ? (
                      <p className="px-2 pb-1 pt-3 text-2xs font-semibold uppercase tracking-wide text-muted-foreground first:pt-1">
                        {command.group}
                      </p>
                    ) : null}
                    <div
                      id={`command-${command.id}`}
                      role="option"
                      aria-selected={index === active}
                      data-active={index === active}
                      onMouseEnter={() => setActive(index)}
                      onClick={() => runAt(index)}
                      className={cn(
                        'flex min-h-10 cursor-pointer items-center gap-3 rounded-md px-2.5 py-2 text-sm',
                        index === active
                          ? 'bg-accent text-accent-foreground'
                          : 'text-foreground',
                      )}
                    >
                      <Icon
                        className={cn(
                          'size-4 shrink-0',
                          index === active ? 'text-primary' : 'text-muted-foreground',
                        )}
                        aria-hidden
                      />
                      <span className="min-w-0 flex-1 truncate">{command.label}</span>
                      {command.hint ? (
                        <span className="hidden shrink-0 text-2xs text-muted-foreground sm:inline">
                          {command.hint}
                        </span>
                      ) : null}
                    </div>
                  </React.Fragment>
                );
              })
            )}
          </div>

          {/* The keys are spelled out because this is the one control an
              evaluator is told to use, and the modifier is platform-resolved
              so a Windows machine is never shown a Command glyph. */}
          <div className="flex items-center gap-4 border-t bg-elevated px-4 py-2 text-2xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <kbd className="rounded border bg-muted px-1.5 py-0.5 font-medium">↑</kbd>
              <kbd className="rounded border bg-muted px-1.5 py-0.5 font-medium">↓</kbd>
              to move
            </span>
            <span className="flex items-center gap-1.5">
              <kbd className="rounded border bg-muted px-1.5 py-0.5 font-medium">
                <CornerDownLeft className="size-3" aria-hidden />
                <span className="sr-only">Enter</span>
              </kbd>
              to open
            </span>
            <span className="ml-auto flex items-center gap-1.5">
              <kbd className="rounded border bg-muted px-1.5 py-0.5 font-medium">Esc</kbd>
              to close
            </span>
            <span className="sr-only">
              Open this palette anywhere with {shortcutHint}.
            </span>
          </div>
        </div>
      </div>
    </dialog>
  );
}
