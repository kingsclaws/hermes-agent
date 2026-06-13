import { useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { PageHeaderContext } from "./page-header-context";
import { ViewToolbar } from "@/components/ViewToolbar";
import { cn } from "@/lib/utils";

export function PageHeaderProvider({
  children,
  pluginTabs,
}: {
  children: ReactNode;
  pluginTabs: { path: string; label: string }[];
}) {
  const { pathname } = useLocation();
  const [titleOverride, setTitleOverride] = useState<string | null>(null);
  const [afterTitle, setAfterTitle] = useState<ReactNode>(null);
  const [toolbarActions, setToolbarActions] = useState<ReactNode>(null);
  const [breadcrumb, setBreadcrumbState] = useState<{ label: string; href?: string }[]>([]);

  useLayoutEffect(() => {
    setTitleOverride(null);
    setAfterTitle(null);
    setToolbarActions(null);
    setBreadcrumbState([]);
  }, [pathname]);

  const value = useMemo(
    () => ({
      setAfterTitle,
      setEnd: setToolbarActions,
      setTitle: setTitleOverride,
      setToolbarActions,
      setBreadcrumb: setBreadcrumbState,
      title: titleOverride,
      afterTitle,
      breadcrumb,
      toolbarActions,
    }),
    [titleOverride, afterTitle, breadcrumb, toolbarActions],
  );

  const isChatRoute = pathname === "/chat" || pathname === "/chat/";

  return (
    <PageHeaderContext.Provider value={value}>
      <div className="flex min-h-0 w-full min-w-0 flex-1 flex-col overflow-hidden">
        <ViewToolbar pluginTabs={pluginTabs} />

        <main
          className={cn(
            "min-h-0 w-full min-w-0 flex-1 flex flex-col",
            isChatRoute
              ? "overflow-hidden"
              : "overflow-y-auto overflow-x-hidden [scrollbar-gutter:stable]",
          )}
        >
          {children}
        </main>
      </div>
    </PageHeaderContext.Provider>
  );
}
