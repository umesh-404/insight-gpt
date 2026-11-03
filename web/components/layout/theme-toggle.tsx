'use client';

import { Moon, Sun } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { useTheme } from '@/lib/theme';

/**
 * The glyph is chosen by CSS (the `dark` class on `<html>`), not by React
 * state. That matters: the class is set by the pre-hydration script, so the
 * correct icon is painted on the very first frame even though the provider
 * cannot know the theme until it has a DOM to read it from.
 *
 * Only the label comes from state, and it starts at the same value the server
 * rendered so hydration still matches.
 */
export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const target = theme === 'dark' ? 'light' : 'dark';
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={toggleTheme}
      aria-label={`Switch to ${target} theme`}
      title={`Switch to ${target} theme`}
    >
      {/* A fixed-size box so the two glyphs swap in place instead of nudging
          the header. `relative` lives here rather than on the button, which
          other controls share. */}
      <span className="relative flex size-4 items-center justify-center" aria-hidden>
        <Sun className="absolute size-4 rotate-0 scale-100 transition-transform duration-150 dark:-rotate-90 dark:scale-0" />
        <Moon className="absolute size-4 rotate-90 scale-0 transition-transform duration-150 dark:rotate-0 dark:scale-100" />
      </span>
    </Button>
  );
}
