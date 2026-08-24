import { DashboardView } from '@/components/dashboard-view';

const TITLES: Record<string, string> = {
  retail: 'Retail overview',
  inventory: 'Inventory health',
  voc: 'Voice of customer',
};

export default function DashboardDetailPage({
  params,
}: {
  params: { id: string };
}) {
  return <DashboardView title={TITLES[params.id] ?? 'Dashboard'} />;
}
