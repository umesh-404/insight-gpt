import {
  BarChart3,
  Database,
  FileText,
  GitBranch,
  MessageSquareText,
  Settings,
  Sparkles,
  type LucideIcon,
} from 'lucide-react';
import type { Role } from '@/lib/types';

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Minimum role required to see the item (docs/07 §3.1). */
  minRole: Role;
  description: string;
}

/**
 * Route ↔ role map (docs/07 §3.1). Nav items the role can't use are hidden;
 * the API re-checks every call, so this gate is UX, not security.
 */
export const NAV_ITEMS: NavItem[] = [
  {
    href: '/ask',
    label: 'Ask',
    icon: MessageSquareText,
    minRole: 'viewer',
    description: 'Conversational analytics',
  },
  {
    href: '/dashboards',
    label: 'Dashboards',
    icon: BarChart3,
    minRole: 'viewer',
    description: 'Governed metrics & trends',
  },
  {
    href: '/insights',
    label: 'Insights',
    icon: Sparkles,
    minRole: 'analyst',
    description: 'Proactive anomaly digest',
  },
  {
    href: '/pipelines',
    label: 'Pipelines',
    icon: GitBranch,
    minRole: 'analyst',
    description: 'Run history & triggers',
  },
  {
    href: '/reports',
    label: 'Reports',
    icon: FileText,
    minRole: 'viewer',
    description: 'Executive reports',
  },
  {
    href: '/sources',
    label: 'Data sources',
    icon: Database,
    minRole: 'admin',
    description: 'Source administration',
  },
  {
    href: '/settings',
    label: 'Settings',
    icon: Settings,
    minRole: 'viewer',
    description: 'Profile & preferences',
  },
];
