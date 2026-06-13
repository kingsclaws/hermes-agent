import { useState, useCallback, useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { FileTreeNode } from "@/lib/file-utils";

export interface TreeNodeProps {
  node: FileTreeNode;
  depth?: number;
  expandedPaths: Set<string>;
  selectedPath: string | null;
  onToggle: (path: string) => void;
  onSelect: (node: FileTreeNode) => void;
  onNavigate: (path: string) => void;
  renderIcon?: (name: string, isDir: boolean) => ReactNode;
  renderBadge?: (path: string) => ReactNode;
  renderActions?: (node: FileTreeNode) => ReactNode;
  renderCheckbox?: (node: FileTreeNode) => ReactNode;
  renderSize?: (node: FileTreeNode) => ReactNode;
  onContextMenu?: (e: React.MouseEvent, node: FileTreeNode) => void;
  searchQuery?: string;
}

export function TreeNode({
  node,
  depth = 0,
  expandedPaths,
  selectedPath,
  onToggle,
  onSelect,
  onNavigate,
  renderIcon,
  renderBadge,
  renderActions,
  renderCheckbox,
  renderSize,
  onContextMenu,
  searchQuery,
}: TreeNodeProps) {
  const isExpanded = expandedPaths.has(node.path);
  const isSelected = selectedPath === node.path;
  const rowRef = useRef<HTMLDivElement>(null);

  const [rowHovered, setRowHovered] = useState(false);

  useEffect(() => {
    if (isSelected && rowRef.current) {
      rowRef.current.scrollIntoView({ block: "nearest" });
    }
  }, [isSelected]);

  const handleClick = useCallback(() => {
    if (node.isDir) {
      onNavigate(node.path);
      onToggle(node.path);
    } else {
      onSelect(node);
    }
  }, [node, onNavigate, onToggle, onSelect]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        handleClick();
      } else if (e.key === "ArrowRight" && node.isDir && !isExpanded) {
        e.preventDefault();
        onToggle(node.path);
      } else if (e.key === "ArrowLeft" && node.isDir && isExpanded) {
        e.preventDefault();
        onToggle(node.path);
      }
    },
    [handleClick, node.isDir, isExpanded, onToggle, node.path],
  );

  const highlightMatch = (text: string, query?: string) => {
    if (!query) return text;
    const idx = text.toLowerCase().indexOf(query.toLowerCase());
    if (idx === -1) return text;
    return (
      <>
        {text.slice(0, idx)}
        <mark className="bg-yellow-500/30 text-current rounded-sm px-0.5">
          {text.slice(idx, idx + query.length)}
        </mark>
        {text.slice(idx + query.length)}
      </>
    );
  };

  return (
    <>
      <div
        ref={rowRef}
        role="treeitem"
        aria-expanded={node.isDir ? isExpanded : undefined}
        aria-selected={isSelected}
        tabIndex={0}
        className={cn(
          "flex items-center gap-1.5 px-1.5 py-0.5 rounded group transition-colors cursor-pointer select-none text-sm",
          isSelected && "bg-primary/10 border-l-2 border-l-primary",
          !isSelected && "hover:bg-muted/5",
        )}
        style={{ paddingLeft: `${depth * 16 + 4}px` }}
        onClick={handleClick}
        onKeyDown={handleKeyDown}
        onContextMenu={(e) => onContextMenu?.(e, node)}
        onMouseEnter={() => setRowHovered(true)}
        onMouseLeave={() => setRowHovered(false)}
      >
        {/* Expand/collapse chevron */}
        <span className="w-4 h-4 flex items-center justify-center shrink-0">
          {node.isDir ? (
            <ChevronRight
              className={cn(
                "h-3.5 w-3.5 text-text-tertiary transition-transform",
                isExpanded && "rotate-90",
              )}
            />
          ) : null}
        </span>

        {/* Checkbox */}
        {renderCheckbox?.(node)}

        {/* Icon */}
        <span className="shrink-0">{renderIcon?.(node.name, node.isDir)}</span>

        {/* Name */}
        <span
          className={cn("flex-1 min-w-0 truncate", node.isDir && "font-medium")}
        >
          {highlightMatch(node.name, searchQuery)}
        </span>

        {/* Status badge */}
        {!node.isDir && renderBadge?.(node.path)}

        {/* Size */}
        {!node.isDir && renderSize?.(node)}

        {/* Actions (on hover) */}
        <div
          className={cn(
            "flex items-center gap-0.5 shrink-0 transition-opacity",
            rowHovered ? "opacity-100" : "opacity-0",
          )}
        >
          {renderActions?.(node)}
        </div>
      </div>

      {/* Children */}
      {node.isDir && isExpanded && node.children.length > 0 && (
        <div role="group">
          {node.children.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              expandedPaths={expandedPaths}
              selectedPath={selectedPath}
              onToggle={onToggle}
              onSelect={onSelect}
              onNavigate={onNavigate}
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
      )}
    </>
  );
}
