import type { Metadata } from 'next';
import { SourcesView } from '@/components/sources-view';

export const metadata: Metadata = {
  title: 'Data sources',
  description:
    'Register, test, and administer the data sources feeding the warehouse and index.',
};

export default function SourcesPage() {
  return <SourcesView />;
}
