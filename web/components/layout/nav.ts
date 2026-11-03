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

/**
 * Sidebar sections.
 *
 * Seven flat items is past the point where a list reads as a list: an
 * evaluator seeing the sidebar for the first time has to read all seven labels
 * to find anything, and "Pipelines" sitting between "Insights" and "Reports"
 * implies they are peers when one is data-engineering plumbing and the others
 * are analysis output.
 *
 * So the nav is grouped by *who is doing what*, which is also how the role
 * gate falls out:
 *
 *  - `analyse`  — the analyst's loop: ask, look, be told, report. Viewer-ish.
 *  - `platform` — how the data got there. Analyst and admin only.
 *  - `account`  — utility. Deliberately not a peer of the two above, and
 *                 rendered next to the user card rather than in the list, so
 *                 Settings never competes with the work for attention.
 */
export type NavGroupId = 'analyse' | 'platform' | 'account';

export interface NavGroup {
  id: NavGroupId;
  /** Shown as the section label; `null` for groups rendered without a header. */
  label: string | null;
}

/** Render order of the two primary sections. `account` is placed separately. */
export const NAV_GROUPS: NavGroup[] = [
  { id: 'analyse', label: 'Analyse' },
  { id: 'platform', label: 'Data platform' },
];

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  /** Minimum role required to see the item (docs/07 §3.1). */
  minRole: Role;
  description: string;
  group: NavGroupId;
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
    group: 'analyse',
  },
  {
    href: '/dashboards',
    label: 'Dashboards',
    icon: BarChart3,
    minRole: 'viewer',
    description: 'Governed metrics & trends',
    group: 'analyse',
  },
  {
    href: '/insights',
    label: 'Insights',
    icon: Sparkles,
    minRole: 'analyst',
    description: 'Proactive anomaly digest',
    group: 'analyse',
  },
  {
    href: '/reports',
    label: 'Reports',
    icon: FileText,
    minRole: 'viewer',
    description: 'Executive reports',
    group: 'analyse',
  },
  {
    href: '/pipelines',
    label: 'Pipelines',
    icon: GitBranch,
    minRole: 'analyst',
    description: 'Run history & triggers',
    group: 'platform',
  },
  {
    href: '/sources',
    label: 'Data sources',
    icon: Database,
    minRole: 'admin',
    description: 'Source administration',
    group: 'platform',
  },
  {
    href: '/settings',
    label: 'Settings',
    icon: Settings,
    minRole: 'viewer',
    description: 'Profile & preferences',
    group: 'account',
  },
];

/**
 * The item a given pathname belongs to, for the header's "you are here" label.
 * Longest match wins, so `/pipelines/runs/42` resolves to Pipelines rather
 * than to whichever item happens to be listed first.
 */
export function navItemForPath(pathname: string): NavItem | undefined {
  let best: NavItem | undefined;
  for (const item of NAV_ITEMS) {
    const matches = pathname === item.href || pathname.startsWith(`${item.href}/`);
    if (!matches) continue;
    if (!best || item.href.length > best.href.length) best = item;
  }
  return best;
}
