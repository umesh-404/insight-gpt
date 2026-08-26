import type { Metadata } from 'next';
import { PipelinesView } from '@/components/pipelines-view';

export const metadata: Metadata = {
  title: 'Pipelines',
  description:
    'Pipeline schedules, run history, and per-stage row counts.',
};

export default function PipelinesPage() {
  return <PipelinesView />;
}
