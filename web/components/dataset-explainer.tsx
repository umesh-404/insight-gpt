/**
 * What actually happens to a file after it is uploaded.
 *
 * This is the first thing an evaluator reads on an empty `/sources`, so it is
 * written as a claim that can be checked rather than a feature list. Every line
 * describes a step that exists in the pipeline, named in the order it runs, and
 * the last line states the one thing that leaves the machine — and nothing
 * more than that one thing.
 */
import {
  FileSearch,
  MessagesSquare,
  ShieldCheck,
  Boxes,
  Info,
} from 'lucide-react';

interface Step {
  icon: React.ReactNode;
  title: string;
  body: string;
}

const STEPS: Step[] = [
  {
    icon: <FileSearch className="size-4" aria-hidden />,
    title: 'Parsed',
    body: 'Text is extracted from the file by the API process on this machine. Tables are read row by row; PDFs and Word files page by page.',
  },
  {
    icon: <ShieldCheck className="size-4" aria-hidden />,
    title: 'Redacted',
    body: 'Recognised secrets and personal data are removed before anything is embedded, and the count is reported on the row. Redaction runs first, every time.',
  },
  {
    icon: <Boxes className="size-4" aria-hidden />,
    title: 'Chunked and embedded locally',
    body: 'The redacted text is split into passages, turned into vectors by the embedding model running on this machine, and written to the local Qdrant index.',
  },
  {
    icon: <MessagesSquare className="size-4" aria-hidden />,
    title: 'Retrievable in Ask',
    body: 'A question in Ask searches those passages, and the answer cites the dataset the passage came from by name.',
  },
];

export function DatasetPipelineExplainer() {
  return (
    <div className="space-y-5">
      <ol className="space-y-4">
        {STEPS.map((step, i) => (
          <li key={step.title} className="flex gap-3">
            <span
              className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground"
              aria-hidden
            >
              {step.icon}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-medium">
                {/* The number is the sequence, which is the whole point of the
                    list; the icon alone cannot say "third". */}
                <span className="mr-1.5 tabular-nums text-muted-foreground">
                  {i + 1}.
                </span>
                {step.title}
              </p>
              <p className="mt-0.5 max-w-[68ch] text-sm leading-relaxed text-muted-foreground">
                {step.body}
              </p>
            </div>
          </li>
        ))}
      </ol>

      {/* A hairline rule and muted type rather than a tinted panel: these are
          limits on a working feature, not a warning that something broke. */}
      <section className="border-l-2 border-border pl-3.5">
        <h4 className="flex items-center gap-1.5 text-2xs font-medium uppercase tracking-[0.12em] text-muted-foreground">
          <Info className="size-3.5" aria-hidden />
          What leaves this machine, and what does not
        </h4>
        <ul className="mt-1.5 space-y-1 text-sm leading-relaxed text-muted-foreground">
          <li className="max-w-[80ch]">
            The file, its extracted text and its vectors stay on this machine.
            Parsing, redaction, embedding and search all run locally.
          </li>
          <li className="max-w-[80ch]">
            The one exception: the passages retrieved for a question are sent, in
            the prompt, to the hosted model that writes the answer. Nothing else
            from the document is transmitted.
          </li>
          <li className="max-w-[80ch]">
            An uploaded table is answered from its text through retrieval. It
            does not become a governed warehouse metric, and a question about it
            is never answered from the retail warehouse figures instead.
          </li>
        </ul>
      </section>
    </div>
  );
}
