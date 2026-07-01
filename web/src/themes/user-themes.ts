/**
 * User-installed theme store — localStorage-backed registry of custom
 * themes that live alongside the 7 built-ins.
 *
 * Modeled on Desktop's `apps/desktop/src/themes/user-themes.ts` but
 * adapted for the browser: no Nano Stores, just a plain reactive module
 * with a subscription callback so `ThemeProvider` can re-render when
 * themes are installed or removed.
 */

import type { DashboardTheme } from "./types";
import { BUILTIN_THEMES } from "./presets";

const STORAGE_KEY = "hermes-dashboard-user-themes-v1";

// ---------------------------------------------------------------------------
// Storage
// ---------------------------------------------------------------------------

function readStored(): Record<string, DashboardTheme> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return {};
    const out: Record<string, DashboardTheme> = {};
    for (const [key, val] of Object.entries(parsed as Record<string, unknown>)) {
      // Never let a stored theme shadow a built-in name.
      if (BUILTIN_THEMES[key]) continue;
      if (!isValidTheme(val)) continue;
      out[key] = val;
    }
    return out;
  } catch {
    return {};
  }
}

function persist(themes: Record<string, DashboardTheme>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(themes));
  } catch {
    // localStorage full or unavailable — silently drop.
  }
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

const REQUIRED_COLOR_KEYS = [
  "background",
  "midground",
] as const;

function isValidTheme(value: unknown): value is DashboardTheme {
  if (!value || typeof value !== "object") return false;
  const t = value as Record<string, unknown>;
  if (typeof t.name !== "string" || !t.name.trim()) return false;
  if (typeof t.label !== "string" || !t.label.trim()) return false;
  if (!t.palette || typeof t.palette !== "object") return false;
  const p = t.palette as Record<string, unknown>;
  for (const key of REQUIRED_COLOR_KEYS) {
    const layer = p[key];
    if (!layer || typeof layer !== "object") return false;
    if (typeof (layer as Record<string, unknown>).hex !== "string") return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Reactive state
// ---------------------------------------------------------------------------

type Listener = () => void;
let _listeners: Set<Listener> = new Set();
let _themes: Record<string, DashboardTheme> = readStored();

function notify(): void {
  for (const fn of _listeners) fn();
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/** Subscribe to theme store changes. Returns an unsubscribe function. */
export function onUserThemesChange(fn: Listener): () => void {
  _listeners.add(fn);
  return () => {
    _listeners.delete(fn);
  };
}

/** Get a snapshot of all user-installed themes. */
export function getUserThemes(): Record<string, DashboardTheme> {
  return { ..._themes };
}

/** Install or update a user theme. Silently ignores built-in name collisions. */
export function installUserTheme(theme: DashboardTheme): boolean {
  if (!isValidTheme(theme)) return false;
  if (BUILTIN_THEMES[theme.name]) return false;
  _themes = { ..._themes, [theme.name]: theme };
  persist(_themes);
  notify();
  return true;
}

/** Remove a user theme by name. */
export function removeUserTheme(name: string): boolean {
  if (!_themes[name]) return false;
  const next = { ..._themes };
  delete next[name];
  _themes = next;
  persist(_themes);
  notify();
  return true;
}

/** Check whether a theme name belongs to a user-installed theme. */
export function isUserTheme(name: string): boolean {
  return name in _themes;
}

/**
 * Resolve a theme name → full DashboardTheme.
 * Looks in built-ins first, then user themes.
 */
export function resolveTheme(name: string): DashboardTheme | undefined {
  return BUILTIN_THEMES[name] ?? _themes[name];
}

/** List all themes: built-ins first (stable order), then user themes. */
export function listAllThemes(): DashboardTheme[] {
  const builtins = Object.values(BUILTIN_THEMES);
  const users = Object.values(_themes);
  return [...builtins, ...users];
}
