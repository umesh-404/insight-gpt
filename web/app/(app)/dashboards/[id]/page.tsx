import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { DashboardView } from '@/components/dashboard-view';

const TITLES: Record<string, string> = {
  retail: 'Retail overview',
  inventory: 'Inventory health',
  voc: 'Voice of customer',
};

export function generateMetadata({ params }: { params: { id: string } }): Metadata {
  return { title: TITLES[params.id] ?? 'Dashboards' };
}

/**
 * Saved dashboards are designed, not built: there is no dashboard resource in
 * the API, so there is nothing that can distinguish one id from another. The
 * route therefore accepts only the three ids it knows about — previously *any*
 * path rendered a dashboard titled "Dashboard", which invented a saved view
 * that does not exist — and says plainly that what it renders is the shared
 * governed overview, not a per-dashboard definition.
 */
export default function DashboardDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const title = TITLES[params.id];
  if (!title) notFound();
  return (
    <DashboardView
      title={title}
      note="Preview — saved dashboards do not have their own definitions yet, so this shows the shared governed overview."
    />
  );
}
