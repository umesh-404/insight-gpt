import type { Metadata } from 'next';
import { DashboardView } from '@/components/dashboard-view';

export const metadata: Metadata = {
  title: 'Dashboards',
  description:
    'Governed KPI tiles, trends, and inventory risk over the semantic metric layer.',
};

export default function DashboardsPage() {
  return <DashboardView />;
}
