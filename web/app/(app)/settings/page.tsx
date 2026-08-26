import type { Metadata } from 'next';
import { SettingsView } from '@/components/settings-view';

export const metadata: Metadata = {
  title: 'Settings',
  description:
    'Profile, appearance, and live system status for every backing service.',
};

export default function SettingsPage() {
  return <SettingsView />;
}
