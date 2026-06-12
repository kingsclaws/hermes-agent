import { useState } from "react";
import {
  Tag,
  X,
  Check,
  FilePenLine,
} from "lucide-react";
import { api, type DocumentMeta } from "@/lib/api";
import { Button } from "@nous-research/ui/ui/components/button";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Spinner } from "@nous-research/ui/ui/components/spinner";

interface DocumentMetadataEditorProps {
  projectId: string;
  filePath: string;
  currentMeta?: DocumentMeta;
  onClose: () => void;
  onSaved: (meta: DocumentMeta) => void;
}

const STATUS_OPTIONS = [
  { value: "draft", label: "Draft", tone: "outline" as const },
  { value: "review", label: "In Review", tone: "warning" as const },
  { value: "final", label: "Final", tone: "success" as const },
  { value: "signed", label: "Signed", tone: "info" as const },
  { value: "archived", label: "Archived", tone: "outline" as const },
];

export function DocumentMetadataEditor({
  projectId,
  filePath,
  currentMeta,
  onClose,
  onSaved,
}: DocumentMetadataEditorProps) {
  const [status, setStatus] = useState(currentMeta?.status ?? "");
  const [notes, setNotes] = useState(currentMeta?.notes ?? "");
  const [tagInput, setTagInput] = useState("");
  const [tags, setTags] = useState<string[]>(currentMeta?.tags ?? []);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      const result = await api.updateFileMeta(projectId, {
        path: filePath,
        status: status || undefined,
        tags: tags.length > 0 ? tags : undefined,
        notes: notes.trim() || undefined,
      });
      onSaved(result.meta);
    } catch {
      // toast handled by parent
    } finally {
      setSaving(false);
    }
  };

  const addTag = () => {
    const trimmed = tagInput.trim();
    if (trimmed && !tags.includes(trimmed)) {
      setTags([...tags, trimmed]);
    }
    setTagInput("");
  };

  return (
    <div className="border border-border rounded-lg bg-card/60 p-3 flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1.5 text-xs text-text-secondary">
          <FilePenLine className="h-3.5 w-3.5" />
          <span>Document Metadata</span>
        </div>
        <Button ghost size="icon" className="h-6 w-6" onClick={onClose}>
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {/* Status */}
      <div>
        <label className="text-xs text-text-tertiary block mb-1">Status</label>
        <div className="flex flex-wrap gap-1">
          {STATUS_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setStatus(status === opt.value ? "" : opt.value)}
              className={`
                px-2 py-0.5 rounded text-xs transition-colors border
                ${status === opt.value
                  ? "border-primary bg-primary/10 text-primary"
                  : "border-border hover:border-text-tertiary text-text-secondary"
                }
              `}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tags */}
      <div>
        <label className="text-xs text-text-tertiary block mb-1">Tags</label>
        <div className="flex items-center gap-1 mb-1 flex-wrap">
          {tags.map((t) => (
            <Badge key={t} tone="outline" className="text-xs flex items-center gap-1">
              <Tag className="h-2.5 w-2.5" />
              {t}
              <button
                type="button"
                onClick={() => setTags(tags.filter((x) => x !== t))}
                className="ml-0.5 hover:text-destructive"
              >
                <X className="h-2.5 w-2.5" />
              </button>
            </Badge>
          ))}
        </div>
        <div className="flex gap-1">
          <input
            value={tagInput}
            onChange={(e) => setTagInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") { e.preventDefault(); addTag(); }
            }}
            placeholder="Add tag..."
            className="flex-1 min-w-0 rounded border border-border bg-background px-2 py-1 text-xs outline-none focus:border-primary/50"
          />
          <Button ghost size="sm" onClick={addTag} disabled={!tagInput.trim()}>
            Add
          </Button>
        </div>
      </div>

      {/* Notes */}
      <div>
        <label className="text-xs text-text-tertiary block mb-1">Notes</label>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          placeholder="Internal notes about this document..."
          className="w-full rounded border border-border bg-background px-2 py-1 text-xs outline-none focus:border-primary/50 resize-none"
        />
      </div>

      {/* Actions */}
      <div className="flex justify-end gap-2">
        <Button ghost size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button size="sm" onClick={handleSave} disabled={saving} prefix={saving ? <Spinner /> : <Check className="h-3.5 w-3.5" />}>
          {saving ? "Saving..." : "Save"}
        </Button>
      </div>
    </div>
  );
}
