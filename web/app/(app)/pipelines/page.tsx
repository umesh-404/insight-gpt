import type { Metadata } from 'next';
import { PipelinesView } from '@/components/pipelines-view';

export const metadata: Metadata = { title: 'Pipelines' };

export default function PipelinesPage() {
  return <PipelinesView />;
}
