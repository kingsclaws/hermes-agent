import { useState } from "react";
import { X, BookOpen, FileText, ChevronUp, ChevronDown, Download } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@nous-research/ui/ui/components/button";
import { Input } from "@nous-research/ui/ui/components/input";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { formatFileSize } from "@/lib/utils";

interface Props {
  projectId: string;
  files: string[];
  onClose(): void;
}

export function BinderDialog({ projectId, files, onClose }: Props) {
  const [ordered, setOrdered] = useState<string[]>(files);
  const [title, setTitle] = useState("Legal Document Binder");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [outputUrl, setOutputUrl] = useState<string | null>(null);
  const [outputSize, setOutputSize] = useState(0);

  const moveUp = (idx: number) => {
    if (idx === 0) return;
    const next = [...ordered];
    [next[idx - 1], next[idx]] = [next[idx], next[idx - 1]];
    setOrdered(next);
  };

  const moveDown = (idx: number) => {
    if (idx === ordered.length - 1) return;
    const next = [...ordered];
    [next[idx], next[idx + 1]] = [next[idx + 1], next[idx]];
    setOrdered(next);
  };

  const handleCreate = async () => {
    if (ordered.length === 0) return;
    setCreating(true);
    setError(null);
    try {
      const result = await api.createBinder(projectId, ordered, title);
      if (result.ok) {
        setOutputUrl(result.output);
        setOutputSize(result.size);
      }
    } catch (e: any) {
      setError(e?.message ?? "Binder creation failed");
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative bg-card border border-border shadow-xl rounded-lg w-full max-w-md mx-4 p-5">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-primary" />
            <h2 className="font-semibold text-sm">Create Binder</h2>
          </div>
          <Button ghost size="icon" className="h-7 w-7" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>

        {outputUrl ? (
          <div className="flex flex-col items-center gap-3 py-4">
            <BookOpen className="h-10 w-10 text-primary" />
            <p className="text-sm">Binder created successfully!</p>
            <p className="text-xs text-text-tertiary">
              {ordered.length} files · {formatFileSize(outputSize)}
            </p>
            <div className="flex gap-2">
              <Button
                onClick={() => {
                  api.downloadProjectFile(projectId, outputUrl);
                }}
                prefix={<Download className="h-3.5 w-3.5" />}
                size="sm"
              >
                Download Binder
              </Button>
              <Button ghost size="sm" onClick={onClose}>
                Close
              </Button>
            </div>
          </div>
        ) : (
          <>
            <div className="flex flex-col gap-3">
              <div>
                <label className="text-xs font-medium text-text-secondary mb-1 block">Binder Title</label>
                <Input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className="text-sm h-8"
                  placeholder="Binder title..."
                />
              </div>

              <div>
                <label className="text-xs font-medium text-text-secondary mb-1 block">
                  Document Order ({ordered.length} files)
                </label>
                <div className="max-h-48 overflow-y-auto border border-border rounded">
                  {ordered.map((f, i) => (
                    <div
                      key={f}
                      className="flex items-center gap-2 px-2 py-1.5 text-xs border-b border-border/50 last:border-b-0"
                    >
                      <span className="text-text-tertiary w-5 text-right shrink-0">{i + 1}.</span>
                      <FileText className="h-3 w-3 text-text-tertiary shrink-0" />
                      <span className="flex-1 truncate font-mono">{f.split("/").pop()}</span>
                      <Button ghost size="icon" className="h-5 w-5" onClick={() => moveUp(i)} disabled={i === 0}>
                        <ChevronUp className="h-3 w-3" />
                      </Button>
                      <Button ghost size="icon" className="h-5 w-5" onClick={() => moveDown(i)} disabled={i === ordered.length - 1}>
                        <ChevronDown className="h-3 w-3" />
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {error && <div className="text-xs text-destructive mt-3">{error}</div>}

            <div className="flex justify-end gap-2 mt-4">
              <Button ghost onClick={onClose} size="sm">
                Cancel
              </Button>
              <Button onClick={handleCreate} disabled={creating || ordered.length === 0} size="sm">
                {creating ? <Spinner /> : "Create Binder"}
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
