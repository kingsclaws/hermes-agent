/**
 * Lightweight syntax highlighter for code blocks in chat.
 * Uses regex-based tokenization — no heavy grammar dependencies.
 * Supports: python, bash/sh, json, javascript/typescript, sql, yaml, diff.
 */

type Token = { text: string; className?: string };

interface LangDef {
  keywords: Set<string>;
  patterns: [RegExp, string][];
}

const LANG_DEFS: Record<string, LangDef> = {
  py: {
    keywords: new Set([
      "def", "class", "return", "if", "elif", "else", "for", "while",
      "import", "from", "as", "try", "except", "finally", "with", "yield",
      "raise", "pass", "break", "continue", "and", "or", "not", "in", "is",
      "None", "True", "False", "async", "await", "lambda", "global", "nonlocal",
    ]),
    patterns: [
      [/("""[\s\S]*?"""|'''[\s\S]*?''')/g, "hl-str"],
      [/("[^"]*"|'[^']*')/g, "hl-str"],
      [/(#.*)$/gm, "hl-comment"],
      [/\b(\d+\.?\d*)\b/g, "hl-num"],
      [/\b(def|class)\s+(\w+)/g, "hl-kw hl-fn"],
      [/@\w+/g, "hl-decorator"],
    ],
  },
  sh: {
    keywords: new Set([
      "if", "then", "else", "elif", "fi", "for", "while", "do", "done",
      "case", "esac", "in", "function", "return", "exit", "export",
      "local", "source", "echo", "cd", "ls", "rm", "cp", "mv", "grep",
    ]),
    patterns: [
      [/(#.*)$/gm, "hl-comment"],
      [/("[^"]*"|'[^']*')/g, "hl-str"],
      [/(\b\w+)=/g, "hl-var"],
      [/\$\{?\w+\}?/g, "hl-var"],
      [/\b(\d+\.?\d*)\b/g, "hl-num"],
    ],
  },
  json: {
    keywords: new Set(["true", "false", "null"]),
    patterns: [
      [/("[^"]*")(\s*:)/g, "hl-key hl-punct"],
      [/("[^"]*")/g, "hl-str"],
      [/\b(\d+\.?\d*)\b/g, "hl-num"],
    ],
  },
  js: {
    keywords: new Set([
      "const", "let", "var", "function", "return", "if", "else", "for",
      "while", "class", "extends", "import", "export", "default", "from",
      "async", "await", "try", "catch", "throw", "new", "this", "typeof",
      "true", "false", "null", "undefined", "interface", "type", "enum",
    ]),
    patterns: [
      [/(`[\s\S]*?`)/g, "hl-str"],
      [/("[^"]*"|'[^']*')/g, "hl-str"],
      [/(\/\/.*)$/gm, "hl-comment"],
      [/\b(\d+\.?\d*)\b/g, "hl-num"],
    ],
  },
  ts: {
    keywords: new Set([
      "const", "let", "var", "function", "return", "if", "else", "for",
      "while", "class", "extends", "implements", "import", "export", "default",
      "from", "async", "await", "try", "catch", "throw", "new", "this", "typeof",
      "true", "false", "null", "undefined", "interface", "type", "enum",
      "readonly", "private", "public", "protected", "abstract", "as",
    ]),
    patterns: [
      [/(`[\s\S]*?`)/g, "hl-str"],
      [/("[^"]*"|'[^']*')/g, "hl-str"],
      [/(\/\/.*)$/gm, "hl-comment"],
      [/\b(\d+\.?\d*)\b/g, "hl-num"],
    ],
  },
  sql: {
    keywords: new Set([
      "SELECT", "FROM", "WHERE", "INSERT", "UPDATE", "DELETE", "CREATE",
      "ALTER", "DROP", "TABLE", "INDEX", "VIEW", "INTO", "VALUES", "SET",
      "JOIN", "LEFT", "RIGHT", "INNER", "OUTER", "ON", "AND", "OR", "NOT",
      "NULL", "IS", "IN", "LIKE", "BETWEEN", "ORDER", "BY", "GROUP", "HAVING",
      "LIMIT", "OFFSET", "AS", "DISTINCT", "COUNT", "SUM", "AVG", "MAX", "MIN",
      "PRIMARY", "KEY", "FOREIGN", "REFERENCES", "CASCADE", "DEFAULT",
    ]),
    patterns: [
      [/('(?:[^']|'')*')/g, "hl-str"],
      [/(--.*)$/gm, "hl-comment"],
      [/\b(\d+\.?\d*)\b/g, "hl-num"],
    ],
  },
  yaml: {
    keywords: new Set(["true", "false", "null", "yes", "no", "on", "off"]),
    patterns: [
      [/(#.*)$/gm, "hl-comment"],
      [/^(\s*)([\w.-]+)(\s*:)/gm, "hl-ws hl-key hl-punct"],
      [/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g, "hl-str"],
      [/\b(\d+\.?\d*)\b/g, "hl-num"],
    ],
  },
  diff: {
    keywords: new Set([]),
    patterns: [
      [/^(\+.*)$/gm, "hl-ins"],
      [/^(-.*)$/gm, "hl-del"],
      [/^(@@.*@@)$/gm, "hl-meta"],
    ],
  },
};

/** Map common aliases to lang keys. */
function resolveLang(lang: string): string {
  const m: Record<string, string> = {
    python: "py", py: "py", python3: "py",
    bash: "sh", sh: "sh", shell: "sh", zsh: "sh",
    json: "json",
    javascript: "js", js: "js", mjs: "js",
    typescript: "ts", ts: "ts", tsx: "ts",
    sql: "sql",
    yaml: "yaml", yml: "yaml",
    diff: "diff", patch: "diff",
  };
  return m[lang.toLowerCase()] ?? lang.toLowerCase();
}

export function highlightCode(code: string, lang: string): string {
  const def = LANG_DEFS[resolveLang(lang)];
  if (!def) return escapeHtml(code);

  const escaped = escapeHtml(code);
  const marked = new Array<{ off: number; end: number; cls: string } | null>(escaped.length).fill(null);

  // Mark character ranges for patterns
  for (const [re, cls] of def.patterns) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(escaped)) !== null) {
      const groups = m.length - 1;
      if (groups > 1) {
        // Multi-group pattern: mark each capture group separately
        let gi = 1;
        let baseCls = cls;
        for (const clsPart of cls.split(" ")) {
          if (gi > groups) break;
          if (m[gi] !== undefined) {
            const gStart = m.index + m[0].indexOf(m[gi]);
            for (let pos = gStart; pos < gStart + m[gi].length; pos++) {
              marked[pos] = { off: gStart, end: gStart + m[gi].length, cls: clsPart };
            }
          }
          gi++;
        }
      } else {
        for (let pos = m.index; pos < m.index + m[0].length; pos++) {
          marked[pos] = { off: m.index, end: m.index + m[0].length, cls };
        }
      }
    }
  }

  // Tokenize: split on pattern boundaries, then keyword-check word tokens
  const spans: Token[] = [];
  let i = 0;
  while (i < escaped.length) {
    if (marked[i]) {
      const { off, end, cls } = marked[i]!;
      if (off === i) {
        spans.push({ text: escaped.slice(off, end), className: cls });
        i = end;
        continue;
      }
    }
    // Plain text run — check for keywords at word boundaries
    let j = i;
    while (j < escaped.length && !marked[j]) j++;
    const plain = escaped.slice(i, j);
    // Split plain text into keyword/non-keyword tokens
    const wordRe = /\b(\w+)\b/g;
    let last = 0;
    let wm: RegExpExecArray | null;
    while ((wm = wordRe.exec(plain)) !== null) {
      if (wm.index > last) spans.push({ text: plain.slice(last, wm.index) });
      if (def.keywords.has(wm[1])) {
        spans.push({ text: wm[1], className: "hl-kw" });
      } else {
        spans.push({ text: wm[1] });
      }
      last = wm.index + wm[1].length;
    }
    if (last < plain.length) spans.push({ text: plain.slice(last) });
    i = j;
  }

  return spans
    .map((s) => (s.className ? `<span class="${s.className}">${s.text}</span>` : s.text))
    .join("");
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
