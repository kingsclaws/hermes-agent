/**
 * Plugin SDK — typed helpers and lifecycle hooks for Hermes dashboard plugins.
 *
 * Plugin authors import these types to get TypeScript support:
 *
 *   const sdk = window.__HERMES_PLUGIN_SDK__;
 *   const { React, hooks, api, components } = sdk;
 *
 * Lifecycle hooks let plugins react to activation, deactivation, and
 * settings changes without wiring their own event listeners:
 *
 *   window.__HERMES_PLUGINS__.registerLifecycle("my-plugin", {
 *     onActivate: () => console.log("Plugin activated"),
 *     onDeactivate: () => console.log("Plugin deactivated"),
 *     onSettingsChange: (settings) => console.log("Settings changed:", settings),
 *   });
 */

export interface PluginLifecycleHooks {
  onActivate?: () => void;
  onDeactivate?: () => void;
  onSettingsChange?: (settings: Record<string, unknown>) => void;
}

export interface PluginSettingsField {
  key: string;
  type: "string" | "number" | "boolean" | "select";
  label: string;
  description?: string;
  default?: unknown;
  options?: Array<{ label: string; value: string }>;
  required?: boolean;
}

export interface PluginSettingsSchema {
  fields: PluginSettingsField[];
}

const _lifecycleHooks = new Map<string, PluginLifecycleHooks>();
const _pluginSettings = new Map<string, Record<string, unknown>>();
const _settingsSchemas = new Map<string, PluginSettingsSchema>();
const _settingsListeners = new Set<() => void>();

function _notifySettings() {
  for (const fn of _settingsListeners) {
    try { fn(); } catch { /* ignore */ }
  }
}

export function registerLifecycle(name: string, hooks: PluginLifecycleHooks): void {
  _lifecycleHooks.set(name, hooks);
}

export function registerSettingsSchema(name: string, schema: PluginSettingsSchema): void {
  _settingsSchemas.set(name, schema);
  _notifySettings();
}

export function getLifecycleHooks(name: string): PluginLifecycleHooks | undefined {
  return _lifecycleHooks.get(name);
}

export function getSettingsSchema(name: string): PluginSettingsSchema | undefined {
  return _settingsSchemas.get(name);
}

export function activatePlugin(name: string): void {
  const hooks = _lifecycleHooks.get(name);
  if (hooks?.onActivate) {
    try { hooks.onActivate(); } catch (e) {
      console.warn(`[plugin:${name}] onActivate error:`, e);
    }
  }
}

export function deactivatePlugin(name: string): void {
  const hooks = _lifecycleHooks.get(name);
  if (hooks?.onDeactivate) {
    try { hooks.onDeactivate(); } catch (e) {
      console.warn(`[plugin:${name}] onDeactivate error:`, e);
    }
  }
}

export function getPluginSettings(name: string): Record<string, unknown> {
  return _pluginSettings.get(name) ?? {};
}

export function setPluginSettings(name: string, settings: Record<string, unknown>): void {
  _pluginSettings.set(name, settings);
  _notifySettings();
  const hooks = _lifecycleHooks.get(name);
  if (hooks?.onSettingsChange) {
    try { hooks.onSettingsChange(settings); } catch (e) {
      console.warn(`[plugin:${name}] onSettingsChange error:`, e);
    }
  }
}

export function updatePluginSetting(name: string, key: string, value: unknown): void {
  const current = getPluginSettings(name);
  setPluginSettings(name, { ...current, [key]: value });
}

export function onSettingsChanged(fn: () => void): () => void {
  _settingsListeners.add(fn);
  return () => _settingsListeners.delete(fn);
}

export function getAllSettingsSchemas(): Map<string, PluginSettingsSchema> {
  return new Map(_settingsSchemas);
}
