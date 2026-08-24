/**
 * Inline script executed before hydration to set the theme class synchronously
 * and avoid a flash of the wrong theme. Kept in a non-client module so the
 * server root layout can import the string without crossing a client boundary.
 */
export const THEME_STORAGE_KEY = 'igpt-theme';

export const themeScript = `
(function () {
  try {
    var stored = localStorage.getItem('${THEME_STORAGE_KEY}');
    var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    var theme = stored || (prefersDark ? 'dark' : 'light');
    if (theme === 'dark') document.documentElement.classList.add('dark');
    document.documentElement.style.colorScheme = theme;
  } catch (e) {}
})();
`;
