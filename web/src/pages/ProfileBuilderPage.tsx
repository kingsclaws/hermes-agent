import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Brain,
  Check,
  Cpu,
  Package,
  Save,
} from "lucide-react";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent } from "@nous-research/ui/ui/components/card";
import { Input } from "@nous-research/ui/ui/components/input";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { api, type SkillInfo, type ModelOptionProvider } from "@/lib/api";
import { useAsync } from "@/hooks/useAsync";
import { useToast } from "@nous-research/ui/hooks/use-toast";
import { Toast } from "@nous-research/ui/ui/components/toast";
import { usePageHeader } from "@/contexts/usePageHeader";
import { cn } from "@/lib/utils";

export default function ProfileBuilderPage() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const { toast, showToast } = useToast();
  const { setEnd } = usePageHeader();

  const [soulText, setSoulText] = useState("");
  const [soulLoading, setSoulLoading] = useState(true);
  const [soulSaving, setSoulSaving] = useState(false);
  const [soulDirty, setSoulDirty] = useState(false);

  const [modelProvider, setModelProvider] = useState("");
  const [modelName, setModelName] = useState("");
  const [modelSaving, setModelSaving] = useState(false);

  const [skillSearch, setSkillSearch] = useState("");

  const { data: skills } = useAsync(() => api.getSkills());
  const { data: modelOptions } = useAsync(() => api.getModelOptions());
  const { data: modelInfo } = useAsync(() => api.getModelInfo());

  useEffect(() => {
    if (!name) return;
    setSoulLoading(true);
    api
      .getProfileSoul(name)
      .then((res) => {
        setSoulText(res.content);
        setSoulDirty(false);
      })
      .catch(() => setSoulText(""))
      .finally(() => setSoulLoading(false));
  }, [name]);

  useEffect(() => {
    if (modelInfo) {
      setModelProvider(modelInfo.provider);
      setModelName(modelInfo.model);
    }
  }, [modelInfo]);

  const handleSaveSoul = useCallback(async () => {
    if (!name) return;
    setSoulSaving(true);
    try {
      await api.updateProfileSoul(name, soulText);
      showToast("SOUL saved", "success");
      setSoulDirty(false);
    } catch (e) {
      showToast(`Failed to save: ${e}`, "error");
    } finally {
      setSoulSaving(false);
    }
  }, [name, soulText, showToast]);

  const handleSetModel = useCallback(async () => {
    if (!modelProvider || !modelName) return;
    setModelSaving(true);
    try {
      await api.setModelAssignment({
        scope: "main",
        provider: modelProvider,
        model: modelName,
      });
      showToast(`Model set: ${modelProvider}/${modelName}`, "success");
    } catch (e) {
      showToast(`Failed to set model: ${e}`, "error");
    } finally {
      setModelSaving(false);
    }
  }, [modelProvider, modelName, showToast]);

  const providers: ModelOptionProvider[] = modelOptions?.providers ?? [];
  const selectedProvider = providers.find((p) => p.slug === modelProvider);
  const availableModels = selectedProvider?.models ?? [];

  const filteredSkills = useMemo(() => {
    if (!skills) return [];
    if (!skillSearch.trim()) return skills;
    const q = skillSearch.toLowerCase();
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.description.toLowerCase().includes(q) ||
        s.category.toLowerCase().includes(q),
    );
  }, [skills, skillSearch]);

  const skillsByCategory = useMemo(() => {
    const map = new Map<string, SkillInfo[]>();
    for (const s of filteredSkills) {
      const cat = s.category || "other";
      const list = map.get(cat) ?? [];
      list.push(s);
      map.set(cat, list);
    }
    return map;
  }, [filteredSkills]);

  useEffect(() => {
    setEnd(
      <Button
        className="uppercase"
        size="sm"
        onClick={handleSaveSoul}
        disabled={soulSaving || !soulDirty}
      >
        <Save className="h-3.5 w-3.5 mr-1" />
        {soulSaving ? "Saving..." : "Save SOUL"}
      </Button>,
    );
    return () => setEnd(null);
  }, [setEnd, handleSaveSoul, soulSaving, soulDirty]);

  if (!name) return null;

  return (
    <div className="flex flex-col gap-6 pb-8">
      <Toast toast={toast} />

      {/* Back + title */}
      <div className="flex items-center gap-3">
        <Button
          ghost
          size="icon"
          onClick={() => navigate("/profiles")}
          aria-label="Back to profiles"
        >
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div>
          <h1 className="font-mondwest text-display text-lg uppercase tracking-wider">
            {name}
          </h1>
          <p className="text-xs text-muted-foreground">Profile Builder</p>
        </div>
      </div>

      {/* Model Assignment */}
      <Card>
        <CardContent className="py-4">
          <div className="flex items-center gap-2 mb-3">
            <Cpu className="h-4 w-4 text-muted-foreground" />
            <span className="font-mondwest text-display text-sm uppercase tracking-wider">
              Model
            </span>
            {modelInfo && (
              <Badge tone="outline" className="ml-2">
                {modelInfo.provider}/{modelInfo.model}
              </Badge>
            )}
          </div>

          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Provider</label>
              <select
                value={modelProvider}
                onChange={(e) => {
                  setModelProvider(e.target.value);
                  setModelName("");
                }}
                className="w-full rounded border border-current/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-primary/50"
              >
                <option value="">Select provider...</option>
                {providers.map((p) => (
                  <option key={p.slug} value={p.slug}>
                    {p.name} {p.is_current ? "(current)" : ""} {p.total_models ? `(${p.total_models} models)` : ""}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="text-xs text-muted-foreground mb-1 block">Model</label>
              {availableModels.length > 0 ? (
                <select
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                  className="w-full rounded border border-current/15 bg-transparent px-3 py-2 text-sm outline-none focus:border-primary/50"
                >
                  <option value="">Select model...</option>
                  {availableModels.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              ) : (
                <Input
                  value={modelName}
                  onChange={(e) => setModelName(e.target.value)}
                  placeholder="Enter model name..."
                />
              )}
            </div>
          </div>

          <div className="mt-3 flex justify-end">
            <Button
              size="sm"
              onClick={handleSetModel}
              disabled={modelSaving || !modelProvider || !modelName}
              className="uppercase"
            >
              {modelSaving ? "Applying..." : "Apply Model"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* SOUL Editor */}
      <Card>
        <CardContent className="py-4">
          <div className="flex items-center gap-2 mb-3">
            <Brain className="h-4 w-4 text-muted-foreground" />
            <span className="font-mondwest text-display text-sm uppercase tracking-wider">
              SOUL (System Prompt)
            </span>
            {soulDirty && (
              <Badge tone="secondary" className="ml-2">unsaved</Badge>
            )}
          </div>

          {soulLoading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">Loading...</div>
          ) : (
            <textarea
              value={soulText}
              onChange={(e) => {
                setSoulText(e.target.value);
                setSoulDirty(true);
              }}
              placeholder="Enter system prompt / SOUL content..."
              className="w-full min-h-[300px] resize-y rounded border border-current/15 bg-transparent px-3 py-2 text-sm font-mono outline-none focus:border-primary/50"
            />
          )}
        </CardContent>
      </Card>

      {/* Skills Overview */}
      <Card>
        <CardContent className="py-4">
          <div className="flex items-center justify-between gap-2 mb-3">
            <div className="flex items-center gap-2">
              <Package className="h-4 w-4 text-muted-foreground" />
              <span className="font-mondwest text-display text-sm uppercase tracking-wider">
                Skills
              </span>
              {skills && (
                <span className="text-xs text-muted-foreground">
                  ({skills.filter((s) => s.enabled).length}/{skills.length} enabled)
                </span>
              )}
            </div>
            <Input
              value={skillSearch}
              onChange={(e) => setSkillSearch(e.target.value)}
              placeholder="Filter skills..."
              className="max-w-xs h-7 text-xs"
            />
          </div>

          {!skills ? (
            <div className="py-8 text-center text-sm text-muted-foreground">Loading skills...</div>
          ) : filteredSkills.length === 0 ? (
            <div className="py-4 text-center text-sm text-muted-foreground">No matching skills</div>
          ) : (
            <div className="space-y-4">
              {Array.from(skillsByCategory.entries()).map(([category, categorySkills]) => (
                <div key={category}>
                  <h3 className="text-xs uppercase tracking-wider text-muted-foreground/70 mb-2">
                    {category}
                  </h3>
                  <div className="grid gap-1.5 sm:grid-cols-2">
                    {categorySkills.map((skill) => (
                      <div
                        key={skill.name}
                        className={cn(
                          "flex items-center gap-2 rounded border px-3 py-2 text-xs",
                          skill.enabled
                            ? "border-success/30 bg-success/5 text-text-primary"
                            : "border-current/10 text-muted-foreground",
                        )}
                      >
                        <span
                          className={cn(
                            "flex h-4 w-4 shrink-0 items-center justify-center rounded-sm border",
                            skill.enabled
                              ? "border-success bg-success/20 text-success"
                              : "border-current/20",
                          )}
                        >
                          {skill.enabled && <Check className="h-2.5 w-2.5" />}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className="truncate font-medium">{skill.name}</div>
                          {skill.description && (
                            <div className="truncate text-[0.6rem] text-muted-foreground">
                              {skill.description}
                            </div>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
