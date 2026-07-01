/**
 * Seed derivation — maps the WebUI 3-layer palette to Desktop-style
 * parametric surface seeds and mix-knob CSS variables.
 *
 * The 3-layer palette (background / midground / foreground) is the theme
 * author's input. This module derives 16 CSS custom properties that the
 * `index.css` cascade consumes to produce a 5-surface hierarchy (chrome →
 * sidebar → card → elevated → bubble) plus accent fill/stroke levels.
 *
 * Surface derivation: each surface seed = mix(background, midground, ratio)
 * where ratio increases for higher-elevation surfaces (they get more
 * midground blended in, making them lighter in dark mode).
 *
 * Mix knobs: control how much of the seed is visible vs. the neutral anchor.
 * Higher % = more seed visible = lighter surface. Tuned per surface to
 * create a visually distinct hierarchy.
 */

import { mix } from "./color";
import type { ThemePalette } from "./types";

/** The 16 CSS custom properties written to :root by paletteToSeeds(). */
export interface ThemeSeeds {
  /** Color seeds — raw hex values that drive the palette cascade. */
  "--theme-background-seed": string;
  "--theme-foreground": string;
  "--theme-primary": string;
  "--theme-midground": string;
  "--theme-warm": string;

  /** Surface seeds — progressively lighter blends of midground into background. */
  "--theme-seed-sidebar": string;
  "--theme-seed-card": string;
  "--theme-seed-elevated": string;
  "--theme-seed-bubble": string;

  /** Mix knobs — % of seed visible vs. neutral (dark-mode defaults). */
  "--theme-mix-chrome": string;
  "--theme-mix-sidebar": string;
  "--theme-mix-card": string;
  "--theme-mix-elevated": string;
  "--theme-mix-bubble": string;

  /** Noise overlay multiplier (0–1.2 range). */
  "--noise-opacity-mul": string;
}

/**
 * Derive Desktop-style seed + knob CSS variables from a 3-layer palette.
 *
 * Surface seeds are created by blending progressively more midground into
 * the background at each elevation level:
 *
 *   chrome   = pure background (deepest)
 *   sidebar  = bg + 2% mg   (barely lifted)
 *   card     = bg + 6% mg   (moderately lifted)
 *   elevated = bg + 10% mg  (most lifted)
 *   bubble   = bg + 4% mg   (between sidebar and card)
 *
 * This matches the current hardcoded percentages in index.css while
 * exposing each surface as an independent variable.
 */
export function paletteToSeeds(palette: ThemePalette): ThemeSeeds {
  const bg = palette.background.hex;
  const mg = palette.midground.hex;

  // Warm glow — the palette stores an rgba() string; pass it through as-is.
  const warm = palette.warmGlow;

  // Noise opacity — default 1.0, themes override via colorOverrides.
  const noise = String(palette.noiseOpacity ?? 1);

  return {
    // ── Color seeds ──────────────────────────────────────────────
    "--theme-background-seed": bg,
    "--theme-foreground": mg,
    "--theme-primary": mg,
    "--theme-midground": mg,
    "--theme-warm": warm,

    // ── Surface seeds (progressive midground blend) ──────────────
    // Higher ratio = more midground in seed = lighter surface.
    // Calibrated for: chrome < bubble < card < elevated.
    // Sidebar is intentionally lighter — Desktop's nous sidebar.
    "--theme-seed-sidebar":      mix(bg, mg, 0.10),
    "--theme-seed-card":         mix(bg, mg, 0.15),
    "--theme-seed-elevated":     mix(bg, mg, 0.28),
    "--theme-seed-bubble":       mix(bg, mg, 0.08),

    // ── Mix knobs (dark-mode) ────────────────────────────────────
    // Higher % = more seed visible vs. the dark neutral.
    "--theme-mix-chrome":    "90%",
    "--theme-mix-sidebar":   "94%",
    "--theme-mix-card":      "88%",
    "--theme-mix-elevated":  "85%",
    "--theme-mix-bubble":    "86%",

    // ── Noise ────────────────────────────────────────────────────
    "--noise-opacity-mul": noise,
  };
}

/**
 * Synthesise light-mode seeds from a dark-only palette.
 *
 * Light mode flips the surface hierarchy: the background is light
 * (near-white, tinted by the theme's midground warmth), surfaces get
 * progressively darker at higher elevations, and text becomes dark.
 * The accent (primary / midground) stays from the dark palette so the
 * theme's identity colour carries across.
 */
export function synthLightSeeds(palette: ThemePalette): ThemeSeeds {
  const mg = palette.midground.hex;
  // Light background — near-white with a whisper of the accent colour.
  const bg = mix("#fafafa", mg, 0.015);
  const warm = palette.warmGlow;
  const noise = String(palette.noiseOpacity ?? 1);

  return {
    // ── Color seeds ──────────────────────────────────────────────
    "--theme-background-seed": bg,
    "--theme-foreground": "#1a1a1a",
    "--theme-primary": mg,
    "--theme-midground": mg,
    "--theme-warm": warm,

    // ── Surface seeds (progressive darkening for elevation) ─────
    // Light mode: higher surfaces get slightly darker / more shadowed.
    "--theme-seed-sidebar":      mix(bg, mg, 0.05),
    "--theme-seed-card":         bg,
    "--theme-seed-elevated":     bg,
    "--theme-seed-bubble":       mix(bg, mg, 0.04),

    // ── Mix knobs (light-mode) ──────────────────────────────────
    "--theme-mix-chrome":    "92%",
    "--theme-mix-sidebar":   "100%",
    "--theme-mix-card":      "22%",
    "--theme-mix-elevated":  "28%",
    "--theme-mix-bubble":    "0%",

    // ── Noise ────────────────────────────────────────────────────
    "--noise-opacity-mul": noise,
  };
}
