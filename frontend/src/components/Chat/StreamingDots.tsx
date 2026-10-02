import { ArcReactor } from '../Hud/ArcReactor';

interface Props {
  phase: string;
}

export function StreamingDots({ phase }: Props) {
  return (
    <div className="flex items-center gap-2.5 py-2">
      <ArcReactor size={22} state="thinking" />
      <span className="hud-readout" style={{ color: 'var(--color-accent)' }}>
        {phase || 'Processing'}
      </span>
      <span className="hud-caret" aria-hidden="true" />
    </div>
  );
}
