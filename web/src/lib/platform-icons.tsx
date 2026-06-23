import {
  Globe,
  MessageSquare,
  Monitor,
  Smartphone,
  Terminal,
  Users,
  type LucideIcon,
} from "lucide-react";

const SOURCE_ICONS: Record<string, LucideIcon> = {
  web: Globe,
  dashboard: Globe,
  telegram: Smartphone,
  discord: Users,
  slack: MessageSquare,
  whatsapp: Smartphone,
  cli: Terminal,
  terminal: Terminal,
  cron: Monitor,
};

export function getPlatformIcon(source: string | null | undefined): LucideIcon {
  if (!source) return MessageSquare;
  return SOURCE_ICONS[source.toLowerCase()] ?? MessageSquare;
}

export function getPlatformLabel(source: string | null | undefined): string {
  if (!source) return "Chat";
  const labels: Record<string, string> = {
    web: "Web",
    dashboard: "Web",
    telegram: "Telegram",
    discord: "Discord",
    slack: "Slack",
    whatsapp: "WhatsApp",
    cli: "CLI",
    terminal: "Terminal",
    cron: "Cron",
  };
  return labels[source.toLowerCase()] ?? source;
}
