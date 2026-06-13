import { createContext } from "react";
import type { ReactNode } from "react";

export interface PageHeaderContextValue {
  // setters
  setAfterTitle: (node: ReactNode) => void;
  /** @deprecated alias for setToolbarActions */
  setEnd: (node: ReactNode) => void;
  setTitle: (title: string | null) => void;
  setToolbarActions: (node: ReactNode) => void;
  setBreadcrumb: (segments: { label: string; href?: string }[]) => void;
  // current values (read by ViewToolbar)
  title: string | null;
  afterTitle: ReactNode;
  breadcrumb: { label: string; href?: string }[];
  toolbarActions: ReactNode;
}

export const PageHeaderContext = createContext<PageHeaderContextValue | null>(
  null,
);
