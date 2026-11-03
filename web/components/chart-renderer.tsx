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
import { formatCurrency, formatNumber, formatPercent, makeAxisTickFormatter } from '@/lib/utils';
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
  /**
   * Visible caption, overriding `spec.title`.
   *
   * Lets a caller own the panel heading here instead of stacking a card header
   * above the figure — one title, one hairline, one row that also carries the
   * chart/table switch.
   */
  title?: string;
}

type Row = Record<string, CellValue>;

/** Series keys become SVG gradient ids; column names may contain spaces. */
function gradientId(key: string): string {
  return `fill-${key.replace(/[^a-zA-Z0-9_-]/g, '-')}`;
}

/**
 * Axis ink is deliberately heavier than the usual `muted-foreground` caption.
 *
 * These charts are read off a projector in a lit room, where 11px grey ticks
 * are the first thing to disappear. Ticks carry the scale, so they get
 * `foreground` at 72% — recessive next to the marks, but still legible when the
 * contrast ratio of the whole image collapses.
 */
const AXIS_TICK = {
  fill: 'hsl(var(--foreground) / 0.72)',
  fontSize: 12,
  fontVariantNumeric: 'tabular-nums',
} as const;
/** Solid hairline, not a dash pattern: dashes shimmer and alias when projected. */
const GRID_STROKE = 'hsl(var(--border))';
const AXIS_LINE = 'hsl(var(--border))';

/**
 * Re-exported so every chart in the app — including the forecast panel, which
 * builds its own ComposedChart — wears the same axis ink and the same solid
 * hairline grid. A second chart with its own lighter ticks and a dashed grid
 * reads as a different product on the same screen.
 */
export const CHART_AXIS_TICK = AXIS_TICK;
export const CHART_GRID_STROKE = GRID_STROKE;
export const CHART_AXIS_LINE = AXIS_LINE;

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

/* -------------------------------------------------------------------------- */
/* Ranked-bar category labels                                                 */
/* -------------------------------------------------------------------------- */

/**
 * Rough advance width of one glyph of the axis face at 12px.
 *
 * Used only to budget the category gutter and the characters that fit in it.
 * Over-estimating is the safe direction: it wraps a line early rather than
 * letting it run under the plot.
 */
const TICK_CHAR_PX = 6.5;

/** Hard ceiling on the category gutter — past this the bars lose their room. */
const MAX_CATEGORY_GUTTER = 164;
const TICK_LINE_HEIGHT = 13;

/**
 * Lay a category label out over at most two lines, breaking on words.
 *
 * The ranked bar chart's reason to exist is that each bar is *identified*, and
 * a single-line tick could not do that: "iPhone 15 Pro Max" arrived as
 * "iPhone 15 Pro Ma…", which is the same label as every other Pro Max variant.
 * Two lines hold every product name the catalog ships; the ellipsis is kept
 * only for the pathological case of one unbreakable word.
 */
function wrapCategory(text: string, charsPerLine: number, maxLines = 2): string[] {
  const clip = (line: string) =>
    line.length > charsPerLine ? `${line.slice(0, Math.max(1, charsPerLine - 1))}…` : line;

  const words = text.split(/\s+/).filter(Boolean);
  if (!words.length) return [text];

  const lines: string[] = [];
  let current = '';
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word;
    if (candidate.length <= charsPerLine) {
      current = candidate;
    } else {
      if (current) lines.push(current);
      current = word;
    }
  }
  if (current) lines.push(current);

  if (lines.length <= maxLines) return lines.map(clip);
  // Everything that did not fit collapses into the last allowed line, so the
  // ellipsis lands at the end of the name rather than mid-way through it.
  const kept = lines.slice(0, maxLines - 1).map(clip);
  kept.push(clip(lines.slice(maxLines - 1).join(' ')));
  return kept;
}

/**
 * Category tick for the horizontal (ranked) bar chart.
 *
 * Recharts' own word-wrap puts a two-line tick on a collision course with its
 * neighbours, so the lines are centred on the band here and the whole label
 * carries an SVG `<title>` — the full name is readable without hunting for the
 * tooltip.
 */
function CategoryTick({
  x,
  y,
  payload,
  charsPerLine,
}: {
  x?: number;
  y?: number;
  payload?: { value?: unknown };
  charsPerLine?: number;
}) {
  const full = formatAxisCategory(payload?.value);
  const lines = wrapCategory(full, charsPerLine ?? 18);
  const anchorX = x ?? 0;
  const top = (y ?? 0) - ((lines.length - 1) * TICK_LINE_HEIGHT) / 2;
  return (
    <text
      x={anchorX}
      y={top}
      textAnchor="end"
      dominantBaseline="central"
      fill={AXIS_TICK.fill}
      fontSize={AXIS_TICK.fontSize}
    >
      <title>{full}</title>
      {lines.map((line, i) => (
        <tspan key={i} x={anchorX} dy={i === 0 ? 0 : TICK_LINE_HEIGHT}>
          {line}
        </tspan>
      ))}
    </text>
  );
}

/** `order_date` → `Order date`. Column names are identifiers, headers are prose. */
function humanizeKey(key: string): string {
  const words = key.replace(/[_-]+/g, ' ').trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : key;
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
export function ChartRenderer({
  spec,
  height = 280,
  className,
  title,
}: ChartRendererProps) {
  const heading = title ?? spec.title;
  const [view, setView] = React.useState<'chart' | 'table'>(
    spec.kind === 'table' ? 'table' : 'chart',
  );
  const data: Row[] = spec.data ?? [];
  const xKey = spec.x ?? 'x';
  const yFormat = spec.options?.yFormat ?? spec.options?.unit;

  // The axis unit is chosen from the largest value PLOTTED, not per tick, so
  // every gridline on one scale reads in the same unit.
  const yMax = React.useMemo(() => {
    let max = 0;
    for (const row of data) {
      for (const s of spec.series) {
        const raw = row[s.y];
        if (typeof raw === 'number' && Number.isFinite(raw)) {
          max = Math.max(max, Math.abs(raw));
        }
      }
    }
    return max;
  }, [data, spec.series]);

  // One unit for the whole axis, chosen from the largest value plotted.
  const yTickFormatter = React.useMemo(
    () => makeAxisTickFormatter(yMax, yFormat),
    [yFormat, yMax],
  );

  if (!data.length) {
    return (
      <div className="flex h-40 items-center justify-center rounded-md border border-dashed text-sm text-muted-foreground">
        No data to visualize.
      </div>
    );
  }

  // The accessible name always exists; the *visible* caption only when the spec
  // names the chart. Most call sites (TrendChart, dashboards, reports) already
  // sit under a card title, so a fallback caption printed "Area chart" directly
  // beneath "Revenue" — a second heading that names the widget instead of the
  // data, and competes with the real one. Nothing is lost by dropping it.
  const caption =
    heading || `${spec.kind[0]?.toUpperCase() ?? ''}${spec.kind.slice(1)} chart`;

  return (
    <figure className={className}>
      <figcaption className="mb-3 flex min-h-8 items-center justify-between gap-3">
        {heading ? (
          <span className="text-base font-semibold leading-none tracking-tight text-foreground">
            {heading}
          </span>
        ) : (
          <span aria-hidden />
        )}
        {/* ≥40px tall so the switch is a real touch target, and labelled in
            words — an icon alone would not say what it switches to. */}
        <button
          type="button"
          onClick={() => setView((v) => (v === 'chart' ? 'table' : 'chart'))}
          className="-mr-1 inline-flex min-h-10 shrink-0 items-center gap-1.5 rounded-md px-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:min-h-8"
          aria-pressed={view === 'table'}
        >
          {view === 'chart' ? (
            <>
              <TableIcon className="size-3.5" aria-hidden /> Data table
            </>
          ) : (
            <>
              <BarChart3 className="size-3.5" aria-hidden /> Chart
            </>
          )}
        </button>
      </figcaption>

      {view === 'table' ? (
        <DataTableView spec={spec} data={data} xKey={xKey} yFormat={yFormat} />
      ) : (
        <div style={{ height }} className="min-w-0" role="img" aria-label={caption}>
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
  // Recharts picks the cursor *element* from the chart type: a band rectangle
  // for bar, a line for line/area/scatter. Passing `fill` to all of them left
  // the continuous charts with the unstyled default stroke, so a hover on the
  // revenue trend showed a grey hairline that matched nothing on the page.
  const bandCursor = kind === 'bar';
  const tooltip = (
    <Tooltip
      content={<ChartTooltip yFormat={fmt.yFormat} />}
      cursor={
        bandCursor
          ? { fill: 'hsl(var(--muted) / 0.4)' }
          : { stroke: 'hsl(var(--foreground) / 0.35)', strokeWidth: 1 }
      }
    />
  );
  const legend =
    series.length > 1 ? (
      <Legend
        iconSize={10}
        wrapperStyle={{ fontSize: 12, color: 'hsl(var(--foreground) / 0.72)' }}
      />
    ) : null;

  switch (kind) {
    case 'line':
      return (
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid stroke={GRID_STROKE} strokeOpacity={0.7} vertical={false} />
          <XAxis
            dataKey={xKey}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: AXIS_LINE }}
            tickFormatter={truncateCategory}
            minTickGap={12}
          />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} tickFormatter={fmt.yTickFormatter} />
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
              activeDot={{ r: 4.5, stroke: 'hsl(var(--background))', strokeWidth: 2 }}
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
          <CartesianGrid stroke={GRID_STROKE} strokeOpacity={0.7} vertical={false} />
          <XAxis
            dataKey={xKey}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: AXIS_LINE }}
            tickFormatter={truncateCategory}
            minTickGap={12}
          />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} tickFormatter={fmt.yTickFormatter} />
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
    case 'bar': {
      // A ranked bar chart over long category names is the one case where the
      // vertical form fails outright: "Top products by revenue" dropped five of
      // its eight product labels to Recharts' tick-collision logic, leaving
      // unidentifiable bars. Turning the chart on its side gives each label a
      // full line of its own, and reading a ranking top-to-bottom is the
      // natural direction anyway. Time series and stacks keep the vertical form.
      const longestCategory = data.reduce(
        (max, row) => Math.max(max, formatAxisCategory(row[xKey]).length),
        0,
      );
      const horizontal =
        series.length === 1 &&
        !spec.stacked &&
        data.length <= 12 &&
        longestCategory > 12;

      if (horizontal) {
        const s = series[0]!;
        // The gutter is sized to the labels actually present, capped so the
        // bars keep the majority of the width; whatever still does not fit is
        // wrapped onto a second line rather than cut.
        const gutter = Math.min(
          MAX_CATEGORY_GUTTER,
          Math.max(96, Math.ceil(longestCategory * TICK_CHAR_PX) + 14),
        );
        const charsPerLine = Math.max(10, Math.floor((gutter - 14) / TICK_CHAR_PX));
        return (
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 4, right: 16, bottom: 4, left: 0 }}
            barCategoryGap="26%"
          >
            <CartesianGrid stroke={GRID_STROKE} strokeOpacity={0.7} horizontal={false} />
            <XAxis
              type="number"
              tick={AXIS_TICK}
              tickLine={false}
              axisLine={{ stroke: AXIS_LINE }}
              tickFormatter={fmt.yTickFormatter}
            />
            <YAxis
              type="category"
              dataKey={xKey}
              tickLine={false}
              axisLine={false}
              width={gutter}
              interval={0}
              tick={<CategoryTick charsPerLine={charsPerLine} />}
            />
            {tooltip}
            <Bar
              dataKey={s.y}
              name={s.label ?? s.y}
              fill={seriesColor(0)}
              radius={[0, 4, 4, 0]}
              maxBarSize={28}
            />
          </BarChart>
        );
      }

      return (
        <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
          <CartesianGrid stroke={GRID_STROKE} strokeOpacity={0.7} vertical={false} />
          <XAxis
            dataKey={xKey}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: AXIS_LINE }}
            tickFormatter={truncateCategory}
            minTickGap={12}
          />
          <YAxis tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} tickFormatter={fmt.yTickFormatter} />
          {tooltip}
          {legend}
          {series.map((s, i) => (
            <Bar
              key={s.y}
              dataKey={s.y}
              name={s.label ?? s.y}
              fill={seriesColor(i)}
              // Rounding every stacked segment makes each one read as a
              // separate bar; only the exposed top of the stack is rounded.
              radius={
                spec.stacked
                  ? i === series.length - 1
                    ? [4, 4, 0, 0]
                    : [0, 0, 0, 0]
                  : [4, 4, 0, 0]
              }
              // A 2px gap in the surface colour keeps touching segments from
              // fusing into one block when two series sit next to each other
              // in the palette.
              stroke={spec.stacked ? 'hsl(var(--background))' : undefined}
              strokeWidth={spec.stacked ? 2 : undefined}
              stackId={spec.stacked ? 'stack' : undefined}
              maxBarSize={48}
            />
          ))}
        </BarChart>
      );
    }
    case 'pie': {
      const nameKey = spec.options?.nameKey ?? 'name';
      const valueKey = spec.options?.valueKey ?? series[0]?.y ?? 'value';
      return (
        // The per-slice labels said exactly what the legend already said, then
        // pushed their leader lines outside the plot and got clipped, which
        // shrank the donut to make room for text nobody could finish reading.
        // One naming layer is enough; the legend keeps it inside the box.
        <PieChart margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
          {tooltip}
          <Legend
            iconSize={10}
            wrapperStyle={{ fontSize: 12, color: 'hsl(var(--foreground) / 0.72)' }}
          />
          <Pie
            data={data}
            dataKey={valueKey}
            nameKey={nameKey}
            innerRadius="48%"
            outerRadius="80%"
            paddingAngle={2}
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
          <CartesianGrid stroke={GRID_STROKE} strokeOpacity={0.7} />
          <XAxis
            type="category"
            dataKey={xKey}
            tick={AXIS_TICK}
            tickLine={false}
            axisLine={{ stroke: AXIS_LINE }}
            tickFormatter={truncateCategory}
          />
          <YAxis type="number" dataKey={series[0]?.y} tick={AXIS_TICK} tickLine={false} axisLine={false} width={64} tickFormatter={fmt.yTickFormatter} />
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
          {/* `product`, `date`, `order_date` — raw column names reach this view
              straight off the wire, and a header reading "order_date" tells the
              reader they are looking at a database rather than a report. */}
          <TableHead>{humanizeKey(categoryKey)}</TableHead>
          {columns.map((c) => (
            <TableHead key={c} className="text-right">
              {spec.series.find((s) => s.y === c)?.label ?? humanizeKey(c)}
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
