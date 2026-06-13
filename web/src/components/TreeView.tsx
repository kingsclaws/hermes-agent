import { useState, useCallback, useMemo } from "react";
import type { ReactNode } from "react";
import { TreeNode } from "@/components/TreeNode";
import { buildFileTree, type FileTreeNode } from "@/lib/file-utils";

export interface TreeViewProps {
  files: {
    name: string;
    path: string;
    size: number;
    modified: string;
    is_dir: boolean;
  }[];
  currentPath?: string;
  selectedPath?: string | null;
  onSelectPath?: (path: string, name: string) => void;
  onNavigate?: (path: string) => void;
  renderIcon?: (name: string, isDir: boolean) => ReactNode;
  renderBadge?: (path: string) => ReactNode;
  renderActions?: (node: FileTreeNode) => ReactNode;
  renderCheckbox?: (node: FileTreeNode) => ReactNode;
  renderSize?: (node: FileTreeNode) => ReactNode;
  onContextMenu?: (e: React.MouseEvent, node: FileTreeNode) => void;
  searchQuery?: string;
  autoExpandDepth?: number;
}

export function TreeView({
  files,
  currentPath = "",
  selectedPath = null,
  onSelectPath,
  onNavigate,
  renderIcon,
  renderBadge,
  renderActions,
  renderCheckbox,
  renderSize,
  onContextMenu,
  searchQuery,
  autoExpandDepth = 1,
}: TreeViewProps) {
  const tree = useMemo(() => buildFileTree(files), [files]);

  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(() => {
    const initial = new Set<string>();
    if (currentPath) {
      const parts: string[] = [];
      for (const p of currentPath.split("/").filter(Boolean)) {
        parts.push(p);
        initial.add(parts.join("/"));
      }
    }
    // Auto-expand up to autoExpandDepth levels
    const expandLevel = (nodes: FileTreeNode[], depth: number) => {
      for (const n of nodes) {
        if (n.isDir && depth < autoExpandDepth) {
          initial.add(n.path);
          expandLevel(n.children, depth + 1);
        }
      }
    };
    expandLevel(tree, 0);
    return initial;
  });

  const handleToggle = useCallback((path: string) => {
    setExpandedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);

  const handleSelect = useCallback(
    (node: FileTreeNode) => {
      if (!node.isDir && onSelectPath) {
        onSelectPath(node.path, node.name);
      }
    },
    [onSelectPath],
  );

  const handleNavigate = useCallback(
    (path: string) => {
      onNavigate?.(path);
    },
    [onNavigate],
  );

  // Filter tree by search query
  const visibleTree = useMemo(() => {
    if (!searchQuery) return tree;
    const q = searchQuery.toLowerCase();
    const filterNodes = (nodes: FileTreeNode[]): FileTreeNode[] => {
      const result: FileTreeNode[] = [];
      for (const n of nodes) {
        const nameMatch = n.name.toLowerCase().includes(q);
        const filteredChildren = filterNodes(n.children);
        if (nameMatch || filteredChildren.length > 0) {
          result.push({ ...n, children: filteredChildren });
        }
      }
      return result;
    };
    return filterNodes(tree);
  }, [tree, searchQuery]);

  // When searching, auto-expand all matched nodes
  const effectiveExpanded = useMemo(() => {
    if (!searchQuery) return expandedPaths;
    const expanded = new Set(expandedPaths);
    const addAll = (nodes: FileTreeNode[]) => {
      for (const n of nodes) {
        if (n.isDir && n.children.length > 0) {
          expanded.add(n.path);
          addAll(n.children);
        }
      }
    };
    addAll(visibleTree);
    return expanded;
  }, [expandedPaths, visibleTree, searchQuery]);

  return (
    <div role="tree" className="flex flex-col gap-0.5 py-1">
      {visibleTree.map((node) => (
        <TreeNode
          key={node.path}
          node={node}
          depth={0}
          expandedPaths={effectiveExpanded}
          selectedPath={selectedPath}
          onToggle={handleToggle}
          onSelect={handleSelect}
          onNavigate={handleNavigate}
          renderIcon={renderIcon}
          renderBadge={renderBadge}
          renderActions={renderActions}
          renderCheckbox={renderCheckbox}
          renderSize={renderSize}
          onContextMenu={onContextMenu}
          searchQuery={searchQuery}
        />
      ))}
    </div>
  );
}
