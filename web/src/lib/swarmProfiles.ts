import {
  Bot,
  FileText,
  Gavel,
  Languages,
  Layout,
  Link,
  PenTool,
  Search,
  type LucideIcon,
} from "lucide-react";

export interface SwarmProfileMeta {
  id: string;
  name: string;
  icon: LucideIcon;
  color: string;
  role: string;
  description: string;
}

const FALLBACK: SwarmProfileMeta = {
  id: "unknown",
  name: "Unknown Agent",
  icon: Bot,
  color: "slate",
  role: "unknown",
  description: "",
};

const PROFILES: SwarmProfileMeta[] = [
  {
    id: "lex-coordinator",
    name: "Coordinator",
    icon: Gavel,
    color: "violet",
    role: "coordinator",
    description: "Orchestrates swarm workers and synthesizes results",
  },
  {
    id: "lex-drafter",
    name: "Drafter",
    icon: PenTool,
    color: "amber",
    role: "drafter",
    description: "Document drafting and editing specialist",
  },
  {
    id: "lex-reviewer-content",
    name: "Content Reviewer",
    icon: Search,
    color: "blue",
    role: "reviewer-content",
    description: "Legal substance, completeness, and consistency review",
  },
  {
    id: "lex-reviewer-format",
    name: "Format Reviewer",
    icon: Layout,
    color: "teal",
    role: "reviewer-format",
    description: "Font, spacing, numbering, and layout compliance",
  },
  {
    id: "lex-reviewer-xref",
    name: "Xref Reviewer",
    icon: Link,
    color: "emerald",
    role: "reviewer-xref",
    description: "Internal citations, bookmarks, and defined terms",
  },
  {
    id: "lex-reviewer-ts",
    name: "TS Reviewer",
    icon: FileText,
    color: "rose",
    role: "reviewer-ts",
    description: "Term sheet and contract consistency review",
  },
  {
    id: "lex-reviewer-translation",
    name: "Translation Reviewer",
    icon: Languages,
    color: "sky",
    role: "reviewer-translation",
    description: "Bilingual accuracy and terminology review",
  },
];

const BY_ID = new Map<string, SwarmProfileMeta>();
for (const p of PROFILES) BY_ID.set(p.id, p);

export function getSwarmProfile(id: string): SwarmProfileMeta {
  return BY_ID.get(id) ?? FALLBACK;
}

export const SWARM_PROFILES: readonly SwarmProfileMeta[] = PROFILES;
