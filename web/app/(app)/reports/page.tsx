import type { Metadata } from 'next';
import { ReportsView } from '@/components/reports-view';

export const metadata: Metadata = {
  title: 'Reports',
  description:
    'Generate and export executive reports built from governed metrics and cited documents.',
};

export default function ReportsPage() {
  return <ReportsView />;
}
