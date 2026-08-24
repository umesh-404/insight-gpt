import type { Metadata } from 'next';
import { DashboardView } from '@/components/dashboard-view';

export const metadata: Metadata = { title: 'Dashboards' };

export default function DashboardsPage() {
  return <DashboardView />;
}
