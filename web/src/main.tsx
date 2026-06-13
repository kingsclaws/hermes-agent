import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import "./index.css";
import App from "./App";
import { SystemActionsProvider } from "./contexts/SystemActions";
import { StatusBarProvider } from "./contexts/StatusBarContext";
import { KeyboardShortcutsProvider } from "./contexts/KeyboardShortcutsContext";
import { DockManagerProvider } from "./contexts/DockManagerContext";
import { I18nProvider } from "./i18n";
import { exposePluginSDK } from "./plugins";
import { ThemeProvider } from "./themes";
import { HERMES_BASE_PATH } from "./lib/api";

// Expose the plugin SDK before rendering so plugins loaded via <script>
// can access React, components, etc. immediately.
exposePluginSDK();

createRoot(document.getElementById("root")!).render(
  <BrowserRouter basename={HERMES_BASE_PATH || undefined}>
    <I18nProvider>
      <ThemeProvider>
        <SystemActionsProvider>
          <KeyboardShortcutsProvider>
            <DockManagerProvider>
              <StatusBarProvider>
                <App />
              </StatusBarProvider>
            </DockManagerProvider>
          </KeyboardShortcutsProvider>
        </SystemActionsProvider>
      </ThemeProvider>
    </I18nProvider>
  </BrowserRouter>,
);
