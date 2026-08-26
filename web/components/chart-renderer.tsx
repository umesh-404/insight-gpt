'use client';

import * as React from 'react';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts';
import { Table as TableIcon, BarChart3 } from 'lucide-react';
// `Cell` is aliased: recharts exports a component with the same name.
import type { Cell as CellValue, ChartSpec, ColumnDtype } from '@/lib/types';
import { seriesColor } from '@/lib/chart-colors';
import { formatCurrency, formatNumber, formatPercent } from '@/lib/utils';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

interface ChartRendererProps {
  spec: ChartSpec;
  height?: number;
  className?: string;
}

type Row = Record<string, CellValue>;

/** Series keys become SVG gradient ids; column names may contain spaces. */
function gradientId(key: string): string {
  return `fill-${key.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
}

const AXIS_TICK = { fill: 'hsl(var(--muted-foreground))', fontSize: 11 };
const GRID_STROKE = 'hsl(var(--border))';

/**
 * Dash patterns paired with the series colours.
 *
 * A multi-series chart that distinguishes lines by hue alone is unreadable for
 * a colour-blind viewer and in greyscale print. The first series stays solid so
 * the common single-series case is unaffected (docs/07 §7).
 */
const SERIES_DASH = [undefined, '5 3', '2 2', '8 3 2 3', '1 3', '10 4'] as const;

function seriesDash(index: number): string | undefined {
  return SERIES_DASH[index % SERIES_DASH.length];
}

const MONTHS = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

/**
 * Shorten axis categories that are really dates.
 *
 * The warehouse returns `date_trunc` output as full ISO timestamps, so an
 * un-formatted month axis reads "2026-02-01T00:00:00" — unreadable at tick
 * size. Anything that is not a recognisable date is returned untouched.
 */
function formatAxisCategory(value: unknown): string {
  if (typeof value !== 'string') return value == null ? '' : String(value);
  const iso = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(value);
  if (!iso) return value;
  const year = iso[1];
  const month = MONTHS[Number(iso[2]) - 1] ?? iso[2];
  const day = iso[3];
  // A day-grain axis repeats the month; a month-grain axis needs the year.
  return day && day !== '01' ? `${month} ${Number(day)}` : `${month} ${year?.slice(2)}`;
}

/** Keep long product names from eating the plot area. */
function truncateCategory(value: unknown): string {
  const text = formatAxisCategory(value);
  return text.length > 16 ? `${text.slice(0, 15)}…` : text;
}

function formatValue(value: unknown, dtype?: ColumnDtype): string {
  if (value == null) return '—';
  if (typeof value !== 'number') return String(value);
  switch (dtype) {
    case 'currency':
      return formatCurrency(value);
    case 'ratio':
      return formatPercent(value);
    default:
      return formatNumber(value, Number.isInteger(value) ? 0 : 2);
  }
}

/** Themed tooltip matching card surfaces. */
function ChartTooltip({
  active,
  payload,
  label,
  yFormat,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; color: string }>;
  label?: string | number;
  yFormat?: ColumnDtype;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="min-w-[9rem] rounded-lg border bg-popover px-3 py-2 text-xs shadow-overlay">
      {label != null && (
        <p className="mb-1.5 border-b pb-1.5 font-medium text-popover-foreground">
          {formatAxisCategory(label)}
        </p>
      )}
      <div className="space-y-1">
        {payload.map((entry) => (
          <div key={entry.name} className="flex items-center gap-2">
            <span
              className="inline-block size-2 shrink-0 rounded-full"
              style={{ backgroundColor: entry.color }}
            />
            <span className="text-muted-foreground">{entry.name}</span>
            <span className="ml-auto font-medium tabular-nums text-popover-foreground">
              {formatValue(entry.value, yFormat)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Maps a declarative `chart_spec` (docs/06 §7) to a Recharts component, pulling
 * every series color from the `--chart-*` tokens. Offers an accessible data-table
 * alternative so charts never rely on color alone (docs/07 §5, §7).
 */
export function ChartRenderer({ spec, height = 280, className }: ChartRendererProps) {
  const [view, setView] = React.useState<'chart' | 'table'>(
    spec.kind === 'table' ? 'table' : 'chart',
  );
  const data: Row[] = spec.data ?? [];
  const xKey = spec.x ?? 'x';
  const yFormat = spec.options?.yFormat ?? spec.options?.unit;

  const yTickFormatter = React.useCallback(
    (value: number) => {
      if (yFormat === 'currency') return formatCurrency(value);
      if (yFormat === 'ratio') return formatPercent(value, 0);
      return formatNumber(value);
    },
    [yFormat],
  );

  if (!data.length) {
    return (
      <div className="flex h-40 items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground">
        No data to visualize.
      </div>
    );
  }

  const caption =
    spec.title || `${spec.kind[0]?.toUpperCase() ?? ''}${spec.kind.slice(1)} chart`;

  return (
    <figure className={className}>
      <figcaption className="mb-2 flex items-center justify-between gap-2">
        <span className="text-sm font-medium text-foreground">{caption}</span>
        <button
          type="button"
          onClick={() => setView((v) => (v === 'chart' ? 'table' : 'chart'))}
          className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
          aria-pressed={view === 'table'}
        >
          {view === 'chart' ? (
            <>
              <TableIcon className="size-3.5" /> Data table
            </>
          ) : (
            <>
              <BarChart3 className="size-3.5" /> Chart
            </>
          )}
        </button>
      </figcaption>

      {view === 'table' ? (
        <DataTableView spec={spec} data={data} xKey={xKey} yFormat={yFormat} />
      ) : (
        <div style={{ height }} role="img" aria-label={caption}>
          <ResponsiveContainer width="100%" height="100%">
            {renderChart(spec, data, xKey, {
              yTickFormatter,
              yFormat,
            })}
          </ResponsiveContainer>
        </div>
      )}
    </figure>
  );
}

function renderChart(
  spec: ChartSpec,
  data: Row[],
  xKey: string,
  fmt: {
    yTickFormatter: (v: number) => string;
    yFormat?: ColumnDtype;
  },
): React.ReactElement {
  const { series, kind } = spec;
  const tooltip = (
    <Tooltip
      content={<ChartTooltip yFormat={fmt.yFormat} />}
      cursor={{ fill: 'hsl(var(--muted) / 0.4)' }}
    />
  );
  const legend =
    series.length > 1 ? (
      <Legend
        wrapperStyle={{ fontSize: 12, color: 'hsl(var(--muted-foreground))' }}
      />
    ) : null;

  switch (kind) {
    case 'line':
      return (
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
          <XAxis
            dataKey={xKey}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: GRID_STROKE }}
            tickFormatter={truncateCategory}
            minTickGap={12}
          />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={56} tickFormatter={fmt.yTickFormatter} />
          {tooltip}
          {legend}
          {series.map((s, i) => (
            <Line
              key={s.y}
              type="monotone"
              dataKey={s.y}
              name={s.label ?? s.y}
              stroke={seriesColor(i)}
              strokeWidth={2}
              // Colour + pattern, so the series survive greyscale and CVD.
              strokeDasharray={series.length > 1 ? seriesDash(i) : undefined}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 0 }}
            />
          ))}
        </LineChart>
      );
    case 'area':
      return (
        <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <defs>
            {series.map((s, i) => (
              <linearGradient key={s.y} id={gradientId(s.y)} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={seriesColor(i)} stopOpacity={0.3} />
                <stop offset="95%" stopColor={seriesColor(i)} stopOpacity={0.02} />
              </linearGradient>
            ))}
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
          <XAxis
            dataKey={xKey}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: GRID_STROKE }}
            tickFormatter={truncateCategory}
            minTickGap={12}
          />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={56} tickFormatter={fmt.yTickFormatter} />
          {tooltip}
          {legend}
          {series.map((s, i) => (
            <Area
              key={s.y}
              type="monotone"
              dataKey={s.y}
              name={s.label ?? s.y}
              stroke={seriesColor(i)}
              strokeWidth={2}
              strokeDasharray={series.length > 1 ? seriesDash(i) : undefined}
              fill={`url(#${gradientId(s.y)})`}
              stackId={spec.stacked ? 'stack' : undefined}
            />
          ))}
        </AreaChart>
      );
    case 'bar':
      return (
        <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} vertical={false} />
          <XAxis
            dataKey={xKey}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: GRID_STROKE }}
            tickFormatter={truncateCategory}
            minTickGap={12}
          />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={56} tickFormatter={fmt.yTickFormatter} />
          {tooltip}
          {legend}
          {series.map((s, i) => (
            <Bar
              key={s.y}
              dataKey={s.y}
              name={s.label ?? s.y}
              fill={seriesColor(i)}
              radius={[4, 4, 0, 0]}
              stackId={spec.stacked ? 'stack' : undefined}
              maxBarSize={48}
            />
          ))}
        </BarChart>
      );
    case 'pie': {
      const nameKey = spec.options?.nameKey ?? 'name';
      const valueKey = spec.options?.valueKey ?? series[0]?.y ?? 'value';
      return (
        <PieChart>
          {tooltip}
          <Legend wrapperStyle={{ fontSize: 12, color: 'hsl(var(--muted-foreground))' }} />
          <Pie
            data={data}
            dataKey={valueKey}
            nameKey={nameKey}
            innerRadius="45%"
            outerRadius="78%"
            paddingAngle={2}
            label={(entry: { name?: string }) => entry.name ?? ''}
          >
            {data.map((_, i) => (
              <Cell key={i} fill={seriesColor(i)} stroke="hsl(var(--background))" strokeWidth={2} />
            ))}
          </Pie>
        </PieChart>
      );
    }
    case 'scatter':
      return (
        <ScatterChart margin={{ top: 8, right: 12, bottom: 8, left: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={GRID_STROKE} />
          <XAxis
            type="category"
            dataKey={xKey}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: GRID_STROKE }}
            tickFormatter={truncateCategory}
          />
          <YAxis type="number" dataKey={series[0]?.y} tick={AXIS_TICK} tickLine={false} axisLine={false} width={56} tickFormatter={fmt.yTickFormatter} />
          <ZAxis range={[60, 60]} />
          {tooltip}
          {series.map((s, i) => (
            <Scatter key={s.y} name={s.label ?? s.y} data={data} fill={seriesColor(i)} />
          ))}
        </ScatterChart>
      );
    default:
      // 'table' kind or unknown → render nothing here; caller shows the table view.
      return <div />;
  }
}

function DataTableView({
  spec,
  data,
  xKey,
  yFormat,
}: {
  spec: ChartSpec;
  data: Row[];
  xKey: string;
  yFormat?: ColumnDtype;
}) {
  const isPie = spec.kind === 'pie';
  const categoryKey = isPie ? (spec.options?.nameKey ?? 'name') : xKey;
  const columns = isPie
    ? [spec.options?.valueKey ?? 'value']
    : spec.series.map((s) => s.y);

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{categoryKey}</TableHead>
          {columns.map((c) => (
            <TableHead key={c} className="text-right">
              {spec.series.find((s) => s.y === c)?.label ?? c}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.map((row, i) => (
          <TableRow key={i}>
            <TableCell className="font-medium">
              {formatAxisCategory(row[categoryKey]) || '—'}
            </TableCell>
            {columns.map((c) => (
              <TableCell key={c} className="text-right tabular-nums">
                {formatValue(row[c], yFormat)}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
