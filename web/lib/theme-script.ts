/**
 * Inline script executed before hydration to set the theme class synchronously
 * and avoid a flash of the wrong theme. Kept in a non-client module so the
 * server root layout can import the string without crossing a client boundary.
 *
 * It also records what it decided on `<html data-theme>`. That attribute is
 * the single source of truth for the resolved theme, and `lib/theme.tsx` reads
 * it instead of re-deriving the answer from localStorage plus a media query.
 *
 * Re-deriving was the bug: two places evaluated the same inputs at two
 * different moments, so the provider could reach a different conclusion than
 * the script had already painted (a stored value written by another tab, or a
 * `prefers-color-scheme` flip between the two reads), and the page visibly
 * changed theme after load. Reading back one recorded decision cannot diverge.
 */
export const THEME_STORAGE_KEY = 'igpt-theme';

/** Attribute the pre-hydration script writes its resolved decision to. */
export const THEME_ATTRIBUTE = 'data-theme';

export const themeScript = `
(function () {
  var theme = 'light';
  try {
    var stored = localStorage.getItem('${THEME_STORAGE_KEY}');
    if (stored === 'light' || stored === 'dark') {
      theme = stored;
    } else if (window.matchMedia('(prefers-color-scheme: dark)').matches) {
      theme = 'dark';
    }
  } catch (e) {
    /* Private mode or blocked storage: fall through to the light default. */
  }
  var root = document.documentElement;
  if (theme === 'dark') root.classList.add('dark');
  root.setAttribute('${THEME_ATTRIBUTE}', theme);
  root.style.colorScheme = theme;
})();
`;
