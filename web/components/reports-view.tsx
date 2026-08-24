'use client';

import * as React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { FileText, Loader2, Plus, Sparkles } from 'lucide-react';
import { PageHeader } from '@/components/layout/page-header';
import { StatusBadge } from '@/components/status-badge';
import { EmptyState, ErrorState } from '@/components/states';
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { useReports } from '@/lib/hooks';
import { useAuth } from '@/lib/auth';
import { api } from '@/lib/api';
import { formatDateTime } from '@/lib/utils';
import type { ReportSection } from '@/lib/types';

const SECTIONS: Array<{ id: ReportSection; label: string }> = [
  { id: 'kpis', label: 'Headline KPIs' },
  { id: 'sales', label: 'Sales decomposition' },
  { id: 'inventory', label: 'Inventory health' },
  { id: 'voice_of_customer', label: 'Voice of customer' },
];

export function ReportsView() {
  const router = useRouter();
  const { toast } = useToast();
  const { hasRole } = useAuth();
  const reports = useReports();
  const canGenerate = hasRole('analyst');

  const [title, setTitle] = React.useState('Q2 2026 Executive Summary');
  const [selected, setSelected] = React.useState<Set<ReportSection>>(
    new Set(['kpis', 'sales', 'voice_of_customer']),
  );
  const [submitting, setSubmitting] = React.useState(false);

  const toggle = (section: ReportSection) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  };

  const onGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || selected.size === 0) return;
    setSubmitting(true);
    try {
      const res = await api.createReport({
        title: title.trim(),
        period: { grain: 'quarter', start: '2026-04-01', end: '2026-06-30' },
        sections: Array.from(selected),
      });
      toast({ title: 'Report generation started', variant: 'success' });
      router.push(`/reports/${res.report_id}`);
    } catch (err) {
      toast({
        title: 'Could not start generation',
        description: err instanceof Error ? err.message : 'Unknown error',
        variant: 'destructive',
      });
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="space-y-6 p-4 sm:p-6">
      <PageHeader
        title="Reports"
        description="Generate cited, exportable executive narratives over a chosen period."
      />

      <div className="grid gap-6 lg:grid-cols-[1fr_340px]">
        {/* Report list */}
        <div className="space-y-3">
          <h2 className="text-lg font-semibold tracking-tight">Your reports</h2>
          {reports.isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full" />
              ))}
            </div>
          ) : reports.isError ? (
            <ErrorState error={reports.error} onRetry={() => void reports.refetch()} />
          ) : reports.data && reports.data.length ? (
            <ul className="space-y-3">
              {reports.data.map((report) => (
                <li key={report.id}>
                  <Link
                    href={`/reports/${report.id}`}
                    className="flex items-center gap-4 rounded-lg border bg-card p-4 transition-colors hover:border-primary/40"
                  >
                    <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <FileText className="size-5" aria-hidden />
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium">{report.title}</p>
                      <p className="text-xs text-muted-foreground">
                        Created {formatDateTime(report.created_at)}
                      </p>
                    </div>
                    <StatusBadge
                      status={report.status === 'ready' ? 'success' : report.status === 'failed' ? 'failed' : 'running'}
                    />
                  </Link>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState
              title="No reports yet"
              description="Generate your first executive report from the panel on the right."
              icon={<FileText className="size-5" />}
            />
          )}
        </div>

        {/* Generate panel */}
        <Card className="h-fit">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Sparkles className="size-4 text-primary" /> Generate report
            </CardTitle>
          </CardHeader>
          <CardContent>
            {canGenerate ? (
              <form onSubmit={onGenerate} className="space-y-4">
                <div className="space-y-1.5">
                  <Label htmlFor="report-title">Title</Label>
                  <Input
                    id="report-title"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                    required
                  />
                </div>
                <div className="space-y-1.5">
                  <Label>Period</Label>
                  <div className="flex items-center gap-2 rounded-md border bg-muted/30 px-3 py-2 text-sm text-muted-foreground">
                    <Badge variant="muted">Quarter</Badge>
                    Apr 1 — Jun 30, 2026
                  </div>
                </div>
                <fieldset className="space-y-2">
                  <legend className="text-sm font-medium">Sections</legend>
                  {SECTIONS.map((section) => (
                    <label
                      key={section.id}
                      className="flex cursor-pointer items-center gap-2.5 rounded-md border p-2.5 text-sm transition-colors hover:bg-accent"
                    >
                      <input
                        type="checkbox"
                        checked={selected.has(section.id)}
                        onChange={() => toggle(section.id)}
                        className="size-4 rounded border-input text-primary focus-visible:ring-2 focus-visible:ring-ring"
                      />
                      {section.label}
                    </label>
                  ))}
                </fieldset>
                <Button
                  type="submit"
                  className="w-full"
                  disabled={submitting || selected.size === 0}
                >
                  {submitting ? (
                    <>
                      <Loader2 className="size-4 animate-spin" /> Generating…
                    </>
                  ) : (
                    <>
                      <Plus className="size-4" /> Generate
                    </>
                  )}
                </Button>
              </form>
            ) : (
              <p className="text-sm text-muted-foreground">
                Report generation requires the analyst role or higher. You can
                still open and read existing reports.
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
