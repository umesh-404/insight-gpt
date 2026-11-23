/**
 * The one `<h1>` on every screen.
 *
 * Description text is width-capped rather than full-bleed: on a wide desktop an
 * unconstrained line of explanatory copy runs to 200 characters and stops being
 * readable, which is precisely where an analytics tool is used.
 */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-6">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-foreground">
          {title}
        </h1>
        {description ? (
          <p className="mt-1.5 max-w-[62ch] text-sm leading-relaxed text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {/* Wraps rather than shrinks: an action row that is allowed to compress
          produces a 60px-wide button with a clipped label at ~700px wide. */}
      {actions ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}
