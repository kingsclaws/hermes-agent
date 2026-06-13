import type { ReactNode } from "react";
import { FileText, File, FolderOpen } from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import type { DocumentMeta } from "@/lib/api";

export function getDocIcon(name: string, isDir: boolean): ReactNode {
  if (isDir) return <FolderOpen className="h-5 w-5 text-warning flex-shrink-0" />;
  const ext = name.split(".").pop()?.toLowerCase();
  switch (ext) {
    case "docx":
      return <FileText className="h-5 w-5 text-[#2B7CD3] flex-shrink-0" />;
    case "pdf":
      return <FileText className="h-5 w-5 text-[#DC2626] flex-shrink-0" />;
    case "jpg": case "jpeg": case "png": case "gif": case "svg": case "webp":
      return <FileText className="h-5 w-5 text-[#8B5CF6] flex-shrink-0" />;
    case "xlsx": case "xls":
      return <FileText className="h-5 w-5 text-[#059669] flex-shrink-0" />;
    default:
      return <File className="h-5 w-5 text-text-tertiary flex-shrink-0" />;
  }
}

export function getDocStatusBadge(inventory: Record<string, DocumentMeta>, filePath: string): ReactNode {
  const meta = inventory[filePath];
  if (!meta?.status && !meta?.signing_status) return null;

  const tones: Record<string, { tone: "outline" | "warning" | "success"; label: string }> = {
    draft: { tone: "outline", label: "Draft" },
    review: { tone: "warning", label: "In Review" },
    final: { tone: "success", label: "Final" },
    signed: { tone: "success", label: "Signed" },
    archived: { tone: "outline", label: "Archived" },
  };
  const t = meta.status ? (tones[meta.status] ?? { tone: "outline" as const, label: meta.status }) : null;
  const signing = meta.signing_status && meta.signing_status !== "unsigned" ? meta.signing_status : null;

  return (
    <span className="flex items-center gap-1 flex-shrink-0">
      {t && <Badge tone={t.tone} className="text-xs">{t.label}</Badge>}
      {signing && (
        <Badge
          tone={
            signing === "signed" ? "success" : signing === "partially-signed" ? "warning" : "outline"
          }
          className="text-[10px]"
        >
          {signing}
        </Badge>
      )}
    </span>
  );
}

export interface FileTreeNode {
  name: string;
  path: string;
  isDir: boolean;
  size: number;
  modified: string;
  children: FileTreeNode[];
}

export function buildFileTree(
  files: { name: string; path: string; size: number; modified: string; is_dir: boolean }[],
): FileTreeNode[] {
  const root: FileTreeNode[] = [];

  for (const f of files) {
    const parts = f.path.split("/");
    let level = root;

    for (let i = 0; i < parts.length; i++) {
      const isLast = i === parts.length - 1;
      const part = parts[i];
      const partPath = parts.slice(0, i + 1).join("/");

      let existing = level.find((n) => n.name === part);
      if (!existing) {
        existing = {
          name: part,
          path: partPath,
          isDir: !isLast || f.is_dir,
          size: isLast ? f.size : 0,
          modified: isLast ? f.modified : "",
          children: [],
        };
        level.push(existing);
      }

      if (isLast) {
        // Override with actual file data
        existing.isDir = f.is_dir;
        existing.size = f.size;
        existing.modified = f.modified;
      }

      level = existing.children;
    }
  }

  // Sort: dirs first, then alpha
  const sortNodes = (nodes: FileTreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1;
      return a.name.localeCompare(b.name);
    });
    for (const n of nodes) sortNodes(n.children);
  };
  sortNodes(root);
  return root;
}
