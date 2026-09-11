export type ThemeMode = 'light' | 'light-soft' | 'light-ice' | 'dark' | 'system';
export type FontScale = 'sm' | 'md' | 'lg' | 'xl';
export type FontFamilyOption =
  | 'system'
  | 'yahei'
  | 'dengxian'
  | 'simhei'
  | 'kaiti'
  | 'simsun'
  | 'fangsong'
  | 'youyuan'
  | 'source'
  | 'pingfang'
  | 'inter';
export type EmojiMode = 'color' | 'mono';

const THEME_KEY = 'miqi-theme';
const FONT_SCALE_KEY = 'miqi-font-scale';
const FONT_FAMILY_KEY = 'miqi-font-family';
export const EMOJI_MODE_KEY = 'miqi-emoji-mode';
export const POINTER_CURSOR_KEY = 'miqi-pointer-cursor';
export const REDUCE_MOTION_KEY = 'miqi-reduce-motion';
export const UI_FONT_SIZE_KEY = 'miqi-ui-font-size';
export const CODE_FONT_SIZE_KEY = 'miqi-code-font-size';
export const CONTRAST_KEY = 'miqi-contrast';
export const SIDEBAR_GLASS_KEY = 'miqi-sidebar-glass';
export const ACCENT_COLOR_KEY = 'miqi-accent-color';
export const BACKGROUND_COLOR_KEY = 'miqi-background-color';
export const FOREGROUND_COLOR_KEY = 'miqi-foreground-color';
export const SURFACE_COLOR_KEY = 'miqi-surface-color';
export const SIDEBAR_COLOR_KEY = 'miqi-sidebar-color';

let systemThemeMedia: MediaQueryList | null = null;
let systemThemeHandler: ((event: MediaQueryListEvent) => void) | null = null;

/** Read a stored preference with a safe fallback when localStorage is unavailable. */
function readStored<T extends string>(key: string, fallback: T): T {
  try {
    return (localStorage.getItem(key) as T | null) ?? fallback;
  } catch {
    return fallback;
  }
}

function readStoredBool(key: string, fallback: boolean): boolean {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : raw === 'true';
  } catch {
    return fallback;
  }
}

function readStoredNumber(key: string, fallback: number): number {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback;
    const value = Number(raw);
    return Number.isFinite(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function parseColor(color: string): [number, number, number] {
  const trimmed = color.trim();
  if (trimmed.startsWith('#')) {
    const hex = trimmed.slice(1);
    if (hex.length === 3) {
      return [
        parseInt(hex[0] + hex[0], 16),
        parseInt(hex[1] + hex[1], 16),
        parseInt(hex[2] + hex[2], 16),
      ];
    }
    if (hex.length === 6) {
      return [
        parseInt(hex.slice(0, 2), 16),
        parseInt(hex.slice(2, 4), 16),
        parseInt(hex.slice(4, 6), 16),
      ];
    }
  }
  const rgb = trimmed.match(/\d+(?:\.\d+)?/g)?.map(Number);
  if (rgb && rgb.length >= 3) return [rgb[0], rgb[1], rgb[2]];
  return [40, 44, 50];
}

function mixColor(from: string, to: string, weight: number): string {
  const [r1, g1, b1] = parseColor(from);
  const [r2, g2, b2] = parseColor(to);
  const t = Math.max(0, Math.min(1, weight));
  return `rgb(${Math.round(r1 + (r2 - r1) * t)}, ${Math.round(
    g1 + (g2 - g1) * t
  )}, ${Math.round(b1 + (b2 - b1) * t)})`;
}

/** Light theme defaults to 45% contrast, dark theme to 60%. */
export function getContrastDefault(theme: ThemeMode): number {
  return theme === 'dark' ? 60 : 45;
}

/** Apply the selected theme to the document root. */
export function applyTheme(mode: ThemeMode) {
  if (typeof window === 'undefined') return;
  const root = document.documentElement;
  root.classList.toggle('light-soft', mode === 'light-soft');
  root.classList.toggle('light-ice', mode === 'light-ice');
  if (mode === 'dark') {
    root.classList.add('dark');
  } else if (mode === 'light' || mode === 'light-soft' || mode === 'light-ice') {
    root.classList.remove('dark');
  } else {
    const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
    root.classList.toggle('dark', prefersDark);
  }
}

/** Keep the system theme in sync while the user stays in `system` mode. */
export function initSystemThemeListener() {
  if (typeof window === 'undefined') return;
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  const handler = (event: MediaQueryListEvent) => {
    if (readStored<ThemeMode>(THEME_KEY, 'system') === 'system') {
      document.documentElement.classList.toggle('dark', event.matches);
    }
  };
  if (systemThemeMedia && systemThemeHandler) {
    systemThemeMedia.removeEventListener('change', systemThemeHandler);
  }
  systemThemeMedia = media;
  systemThemeHandler = handler;
  media.addEventListener('change', handler);
}

/** Apply scale and family font preferences to the document root. */
export function applyFontPreferences(scale: FontScale, family: FontFamilyOption) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.dataset.fontScale = scale;
  root.dataset.fontFamily = family;
}

/** Apply color or monochrome emoji presentation. */
export function applyEmojiPreference(mode: EmojiMode) {
  if (typeof document === 'undefined') return;
  document.documentElement.dataset.emojiMode = mode;
}

/** Apply user-picked colors on top of the active theme. */
export function applyColorPreferences(colors: {
  accent?: string;
  background?: string;
  foreground?: string;
  surface?: string;
  sidebar?: string;
}) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  if (colors.accent !== undefined) {
    if (!colors.accent) {
      root.style.removeProperty('--accent');
      root.style.removeProperty('--accent-hover');
      root.style.removeProperty('--accent-soft');
      root.style.removeProperty('--accent-strong');
    } else {
      root.style.setProperty('--accent', colors.accent);
      root.style.setProperty('--accent-hover', `color-mix(in srgb, ${colors.accent} 82%, #000)`);
      root.style.setProperty(
        '--accent-soft',
        `color-mix(in srgb, ${colors.accent} 16%, transparent)`
      );
      root.style.setProperty('--accent-strong', `color-mix(in srgb, ${colors.accent} 58%, #000)`);
    }
  }
  if (colors.background !== undefined) {
    if (!colors.background) {
      root.style.removeProperty('--background');
    } else {
      root.style.setProperty('--background', colors.background);
    }
  }
  // Foreground only owns --text/--topbar-text; the derived muted/faint shades
  // are owned by applyUIPreferences so the contrast slider can drive them.
  if (colors.foreground) {
    root.style.setProperty('--text', colors.foreground);
    root.style.setProperty('--topbar-text', colors.foreground);
  }
  if (colors.surface !== undefined) {
    if (!colors.surface) {
      root.style.removeProperty('--surface');
      root.style.removeProperty('--surface-muted');
      root.style.removeProperty('--surface-hover');
      root.style.removeProperty('--surface-elevated');
      root.style.removeProperty('--panel-bg');
      root.style.removeProperty('--sidebar-bg');
      root.style.removeProperty('--topbar-bg');
    } else {
      root.style.setProperty('--surface', colors.surface);
      root.style.setProperty('--surface-muted', `color-mix(in srgb, ${colors.surface} 90%, #000)`);
      root.style.setProperty('--surface-hover', `color-mix(in srgb, ${colors.surface} 94%, #fff)`);
      root.style.setProperty(
        '--surface-elevated',
        `color-mix(in srgb, ${colors.surface} 96%, #fff)`
      );
      root.style.setProperty('--panel-bg', colors.surface);
      root.style.setProperty('--sidebar-bg', colors.surface);
      root.style.setProperty('--topbar-bg', colors.surface);
    }
  }
  if (colors.sidebar !== undefined) {
    if (!colors.sidebar) {
      root.style.removeProperty('--sidebar-bg');
      root.style.removeProperty('--sidebar-border');
      return;
    }
    root.style.setProperty('--sidebar-bg', colors.sidebar);
    root.style.setProperty('--sidebar-border', `color-mix(in srgb, ${colors.sidebar} 90%, #000)`);
  }
}

/** Apply ChatGPT-style appearance preferences that map to CSS data attributes. */
export function applyUIPreferences(prefs: {
  pointerCursor: boolean;
  reduceMotion: boolean;
  uiFontSize: number;
  codeFontSize: number;
  contrast: number;
  sidebarGlass: boolean;
}) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  root.dataset.pointerCursor = String(prefs.pointerCursor);
  root.dataset.reduceMotion = String(prefs.reduceMotion);
  root.dataset.sidebarGlass = String(prefs.sidebarGlass);
  root.dataset.contrast = String(prefs.contrast);
  root.style.fontSize = `${prefs.uiFontSize}px`;
  root.style.setProperty('--code-font-size', `${prefs.codeFontSize}px`);
  const contrast = prefs.contrast;
  root.style.setProperty('--ui-contrast', `${contrast}%`);

  const isDark = root.classList.contains('dark');
  const defaultContrast = getContrastDefault(isDark ? 'dark' : 'light');
  const derivedVars = [
    '--text-muted',
    '--text-faint',
    '--placeholder',
    '--topbar-muted-text',
  ] as const;
  // With a custom foreground the derived colors come from applyColorPreferences;
  // only clear them when the user relies on the theme defaults.
  const hasCustomForeground = readStored<string>(FOREGROUND_COLOR_KEY, '') !== '';
  if (contrast === defaultContrast && !hasCustomForeground) {
    for (const v of derivedVars) root.style.removeProperty(v);
    root.style.removeProperty('--text');
    return;
  }

  const computed = getComputedStyle(root);
  const fg = computed.getPropertyValue('--text').trim() || '#121212';
  const bg = computed.getPropertyValue('--background').trim() || '#ffffff';
  // Clamp so secondary text never fully disappears (0% would equal the background).
  const ratio = Math.min(1, Math.max(0.25, contrast / 100));
  const muted = isDark ? mixColor(bg, fg, 0.4 + ratio * 0.6) : mixColor(bg, fg, ratio);
  const faint = isDark ? mixColor(bg, fg, 0.24 + ratio * 0.56) : mixColor(bg, fg, ratio * 0.55);
  const placeholder = isDark
    ? mixColor(bg, fg, 0.18 + ratio * 0.48)
    : mixColor(bg, fg, ratio * 0.45);
  root.style.setProperty('--text-muted', muted);
  root.style.setProperty('--text-faint', faint);
  root.style.setProperty('--placeholder', placeholder);
  root.style.setProperty('--topbar-muted-text', faint);
  // Main text also responds to contrast: below the default it fades toward the
  // background (clamped so it never fully disappears); at/above default it stays
  // at full strength from the stylesheet. A custom foreground is left untouched.
  if (!hasCustomForeground) {
    if (contrast < defaultContrast) {
      root.style.setProperty(
        '--text',
        mixColor(bg, fg, Math.max(0.25, contrast / defaultContrast))
      );
    } else {
      root.style.removeProperty('--text');
    }
  }
}

/** Restore all persisted UI preferences before the first render. */
export function applyStoredUiPreferences() {
  const theme = readStored<ThemeMode>(THEME_KEY, 'system');
  applyTheme(theme);
  // Resolve the effective theme so the default contrast matches what is actually on screen
  // (system + dark OS must default to the dark contrast, not the light one).
  const isDark =
    theme === 'dark' ||
    (theme === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
  applyFontPreferences(
    readStored<FontScale>(FONT_SCALE_KEY, 'md'),
    readStored<FontFamilyOption>(FONT_FAMILY_KEY, 'system')
  );
  applyEmojiPreference(readStored<EmojiMode>(EMOJI_MODE_KEY, 'color'));
  // applyUIPreferences owns the derived text colors (--text-muted etc.); run it
  // first so applyColorPreferences can re-apply foreground-derived values on top.
  applyUIPreferences({
    pointerCursor: readStoredBool(POINTER_CURSOR_KEY, true),
    reduceMotion: readStoredBool(REDUCE_MOTION_KEY, false),
    uiFontSize: readStoredNumber(UI_FONT_SIZE_KEY, 14),
    codeFontSize: readStoredNumber(CODE_FONT_SIZE_KEY, 14),
    contrast: readStoredNumber(CONTRAST_KEY, getContrastDefault(isDark ? 'dark' : 'light')),
    sidebarGlass: readStoredBool(SIDEBAR_GLASS_KEY, true),
  });
  applyColorPreferences({
    accent: readStored<string>(ACCENT_COLOR_KEY, ''),
    background: readStored<string>(BACKGROUND_COLOR_KEY, ''),
    foreground: readStored<string>(FOREGROUND_COLOR_KEY, ''),
    surface: readStored<string>(SURFACE_COLOR_KEY, ''),
    sidebar: readStored<string>(SIDEBAR_COLOR_KEY, ''),
  });
  initSystemThemeListener();
}
