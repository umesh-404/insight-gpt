import type { Metadata } from 'next';
import { SourcesView } from '@/components/sources-view';

export const metadata: Metadata = {
  title: 'Data sources',
  description:
    'Upload files for local retrieval, and administer the connectors feeding the warehouse and index.',
};

export default function SourcesPage() {
  return <SourcesView />;
}
