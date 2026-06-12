import { useState, useEffect } from "react";
import { Search, X, Paperclip, FileText } from "lucide-react";
import { Input } from "@nous-research/ui/ui/components/input";
import { Button } from "@nous-research/ui/ui/components/button";
import { Checkbox } from "@nous-research/ui/ui/components/checkbox";
import { api } from "@/lib/api";

interface Props {
  projectId: string;
  selected: string[];
  onApply(refs: string[]): void;
  onClose(): void;
}

export function DocumentLinkPicker({ projectId, selected, onApply, onClose }: Props) {
  const [files, setFiles] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set(selected));

  useEffect(() => {
    api.fetchProjectFiles(projectId)
      .then((r) => setFiles((r?.files ?? []).map((f) => f.path)))
      .catch(() => setFiles([]))
      .finally(() => setLoading(false));
  }, [projectId]);

  const toggle = (f: string) => {
    const next = new Set(picked);
    if (next.has(f)) next.delete(f);
    else next.add(f);
    setPicked(next);
  };

  const needle = query.trim().toLowerCase();
  const filtered = needle
    ? files.filter((f) => f.toLowerCase().includes(needle))
    : files;

  return (
    <div className="bg-card border border-border rounded-md shadow-lg p-3 w-72">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-medium">Link Documents</span>
        <Button ghost size="icon" className="h-5 w-5" onClick={onClose}>
          <X className="h-3 w-3" />
        </Button>
      </div>

      <div className="relative mb-2">
        <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3 w-3 text-muted-foreground" />
        <Input
          autoFocus
          placeholder="Filter files..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="pl-6 h-7 text-xs"
        />
      </div>

      <div className="max-h-48 overflow-y-auto mb-2">
        {loading && <div className="text-xs text-muted-foreground p-2">Loading...</div>}
        {!loading && filtered.length === 0 && (
          <div className="text-xs text-muted-foreground p-2 italic">No files found</div>
        )}
        {filtered.map((f) => (
          <label key={f} className="flex items-center gap-2 py-1 px-1 hover:bg-secondary/40 rounded cursor-pointer text-xs">
            <Checkbox
              checked={picked.has(f)}
              onCheckedChange={() => toggle(f)}
            />
            <FileText className="h-3 w-3 text-text-tertiary shrink-0" />
            <span className="truncate">{f}</span>
          </label>
        ))}
      </div>

      <div className="flex justify-end gap-2">
        <Button ghost size="sm" className="text-xs h-6" onClick={onClose}>
          Cancel
        </Button>
        <Button
          size="sm"
          className="text-xs h-6"
          onClick={() => onApply([...picked])}
        >
          <Paperclip className="h-3 w-3 mr-1" />
          Link {picked.size > 0 ? `(${picked.size})` : ""}
        </Button>
      </div>
    </div>
  );
}
