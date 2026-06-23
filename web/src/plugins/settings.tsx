import { useCallback, useEffect, useState } from "react";
import { Settings } from "lucide-react";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Label } from "@nous-research/ui/ui/components/label";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import {
  type PluginSettingsSchema,
  type PluginSettingsField,
  getPluginSettings,
  updatePluginSetting,
  getAllSettingsSchemas,
  onSettingsChanged,
} from "./sdk";

interface PluginSettingsProps {
  pluginName: string;
}

export function PluginSettings({ pluginName }: PluginSettingsProps) {
  const [schema, setSchema] = useState<PluginSettingsSchema | null>(null);
  const [settings, setSettings] = useState<Record<string, unknown>>({});

  useEffect(() => {
    const schemas = getAllSettingsSchemas();
    setSchema(schemas.get(pluginName) ?? null);
    setSettings(getPluginSettings(pluginName));

    return onSettingsChanged(() => {
      const schemas = getAllSettingsSchemas();
      setSchema(schemas.get(pluginName) ?? null);
      setSettings(getPluginSettings(pluginName));
    });
  }, [pluginName]);

  const handleChange = useCallback(
    (key: string, value: unknown) => {
      updatePluginSetting(pluginName, key, value);
    },
    [pluginName],
  );

  if (!schema || schema.fields.length === 0) return null;

  return (
    <Card>
      <CardContent className="py-4">
        <div className="flex items-center gap-2 mb-3">
          <Settings className="h-4 w-4 text-muted-foreground" />
          <span className="text-xs uppercase tracking-wider font-medium text-muted-foreground">
            Plugin Settings
          </span>
        </div>

        <div className="space-y-3">
          {schema.fields.map((field) => (
            <SettingsField
              key={field.key}
              field={field}
              value={settings[field.key] ?? field.default}
              onChange={(v) => handleChange(field.key, v)}
            />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

function SettingsField({
  field,
  value,
  onChange,
}: {
  field: PluginSettingsField;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  switch (field.type) {
    case "boolean":
      return (
        <div className="flex items-center gap-2.5">
          <Checkbox
            id={`plugin-${field.key}`}
            checked={value === true}
            onCheckedChange={(checked) => onChange(checked === true)}
          />
          <div>
            <Label
              htmlFor={`plugin-${field.key}`}
              className="text-sm cursor-pointer"
            >
              {field.label}
            </Label>
            {field.description && (
              <p className="text-xs text-muted-foreground">{field.description}</p>
            )}
          </div>
        </div>
      );

    case "select":
      return (
        <div className="grid gap-1.5">
          <Label htmlFor={`plugin-${field.key}`} className="text-sm">
            {field.label}
          </Label>
          <select
            id={`plugin-${field.key}`}
            value={String(value ?? "")}
            onChange={(e) => onChange(e.target.value)}
            className="w-full rounded border border-current/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-primary/50"
          >
            <option value="">Select...</option>
            {field.options?.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          {field.description && (
            <p className="text-xs text-muted-foreground">{field.description}</p>
          )}
        </div>
      );

    case "number":
      return (
        <div className="grid gap-1.5">
          <Label htmlFor={`plugin-${field.key}`} className="text-sm">
            {field.label}
          </Label>
          <Input
            id={`plugin-${field.key}`}
            type="number"
            value={String(value ?? "")}
            onChange={(e) => onChange(Number(e.target.value))}
          />
          {field.description && (
            <p className="text-xs text-muted-foreground">{field.description}</p>
          )}
        </div>
      );

    case "string":
    default:
      return (
        <div className="grid gap-1.5">
          <Label htmlFor={`plugin-${field.key}`} className="text-sm">
            {field.label}
          </Label>
          <Input
            id={`plugin-${field.key}`}
            value={String(value ?? "")}
            onChange={(e) => onChange(e.target.value)}
          />
          {field.description && (
            <p className="text-xs text-muted-foreground">{field.description}</p>
          )}
        </div>
      );
  }
}
