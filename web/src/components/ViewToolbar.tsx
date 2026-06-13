import { useMemo } from "react";
import { Link, useLocation } from "react-router-dom";
import { usePageHeader } from "@/contexts/usePageHeader";
import { resolvePageTitle } from "@/lib/resolve-page-title";
import { cn } from "@/lib/utils";
import { useI18n } from "@/i18n";

export function ViewToolbar({
  pluginTabs,
}: {
  pluginTabs: { path: string; label: string }[];
}) {
  const { pathname } = useLocation();
  const { t } = useI18n();
  const { breadcrumb, toolbarActions, title: titleOverride, afterTitle } = usePageHeader();

  const defaultTitle = useMemo(
    () => resolvePageTitle(pathname, t, pluginTabs),
    [pathname, t, pluginTabs],
  );
  const displayTitle = titleOverride ?? defaultTitle;

  const isEnvRoute = pathname === "/env" || pathname.startsWith("/env/");

  return (
    <header
      className={cn(
        "z-1 flex w-full shrink-0 items-center gap-2",
        "h-10 min-h-[2.5rem]",
        "border-b border-current/20 bg-background-base/40 backdrop-blur-sm",
        "px-3",
      )}
      role="banner"
    >
      {/* Breadcrumb or page title */}
      <div className="flex min-w-0 flex-1 items-center gap-1.5 text-[0.75rem]">
        {breadcrumb && breadcrumb.length > 0 ? (
          <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1">
            {breadcrumb.map((seg, i) => (
              <span key={i} className="flex items-center gap-1 min-w-0">
                {i > 0 && <span className="text-current/30 shrink-0">/</span>}
                {seg.href ? (
                  <Link
                    to={seg.href}
                    className="text-current/60 hover:text-current truncate max-w-[200px] transition-colors"
                  >
                    {seg.label}
                  </Link>
                ) : (
                  <span className="truncate max-w-[200px]">{seg.label}</span>
                )}
              </span>
            ))}
          </nav>
        ) : (
          <h1
            className={cn(
              "font-expanded min-w-0 text-[0.75rem] font-bold tracking-[0.08em] text-midground truncate",
            )}
            style={{ mixBlendMode: "plus-lighter" }}
          >
            {displayTitle}
          </h1>
        )}

        {afterTitle && (
          <div
            className={cn(
              "min-w-0 shrink-0",
              isEnvRoute ? "sm:flex-1 overflow-x-auto" : "overflow-visible",
            )}
          >
            {afterTitle}
          </div>
        )}
      </div>

      {/* Toolbar actions right zone */}
      {toolbarActions && (
        <div className="flex shrink-0 items-center gap-1.5">{toolbarActions}</div>
      )}
    </header>
  );
}
