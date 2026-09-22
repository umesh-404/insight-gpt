import type { Metadata } from 'next';
import { InsightsView } from '@/components/insights-view';

export const metadata: Metadata = {
  title: 'Insights',
  description:
    'Automatically detected anomalies with deterministic root causes and supporting evidence.',
};

export default function InsightsPage() {
  return <InsightsView />;
}
