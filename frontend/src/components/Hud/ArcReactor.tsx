import { useId } from 'react';
import type { CSSProperties } from 'react';

export type ReactorState = 'idle' | 'thinking' | 'speaking';

interface Props {
  /** Rendered width/height: a pixel number or any CSS length (e.g. clamp()). */
  size?: number | string;
  state?: ReactorState;
  className?: string;
  style?: CSSProperties;
}

const CENTER = 100;

/** Tick marks every 5 degrees; every 30 degrees is a long tick. */
const TICKS = Array.from({ length: 72 }, (_, i) => {
  const angle = (i * 5 * Math.PI) / 180;
  const major = i % 6 === 0;
  const outer = 97;
  const inner = major ? 89 : 93;
  return {
    x1: CENTER + outer * Math.cos(angle),
    y1: CENTER + outer * Math.sin(angle),
    x2: CENTER + inner * Math.cos(angle),
    y2: CENTER + inner * Math.sin(angle),
    major,
  };
});

/** Stroke-dasharray that splits a circle into `count` arcs of `fill` (0-1). */
function segments(radius: number, count: number, fill: number): string {
  const step = (2 * Math.PI * radius) / count;
  return `${(step * fill).toFixed(2)} ${(step * (1 - fill)).toFixed(2)}`;
}

/**
 * The HUD "core": concentric rings that spin at different speeds around a
 * glowing centre. Purely decorative; motion is CSS-driven (see `.reactor` in
 * index.css) and `state` only changes the tempo.
 */
export function ArcReactor({ size = 120, state = 'idle', className = '', style }: Props) {
  const gradientId = `reactor-core-${useId().replace(/:/g, '')}`;
  const dimension = typeof size === 'number' ? `${size}px` : size;

  return (
    <svg
      viewBox="0 0 200 200"
      className={`reactor ${className}`}
      data-state={state}
      style={{ width: dimension, height: dimension, ...style }}
      aria-hidden="true"
      focusable="false"
    >
      <defs>
        <radialGradient id={gradientId}>
          <stop offset="0%" style={{ stopColor: '#ffffff', stopOpacity: 1 }} />
          <stop offset="28%" style={{ stopColor: '#d4fbff', stopOpacity: 0.95 }} />
          <stop offset="58%" style={{ stopColor: 'var(--color-accent)', stopOpacity: 0.75 }} />
          <stop offset="100%" style={{ stopColor: 'var(--color-accent)', stopOpacity: 0 }} />
        </radialGradient>
      </defs>

      {/* Outer tick ring */}
      <g className="r-spin r-ticks" stroke="currentColor">
        {TICKS.map((t, i) => (
          <line
            key={i}
            x1={t.x1}
            y1={t.y1}
            x2={t.x2}
            y2={t.y2}
            strokeWidth={t.major ? 1.6 : 0.8}
            opacity={t.major ? 0.9 : 0.45}
          />
        ))}
      </g>

      <circle cx={CENTER} cy={CENTER} r={86} fill="none" stroke="currentColor" strokeWidth={0.6} opacity={0.35} />

      {/* Dashed ring, counter-rotating */}
      <circle
        className="r-spin r-dash"
        cx={CENTER}
        cy={CENTER}
        r={80}
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeDasharray="2 5"
        opacity={0.6}
      />

      {/* Two scanner arcs */}
      <circle
        className="r-spin r-scan"
        cx={CENTER}
        cy={CENTER}
        r={72}
        fill="none"
        stroke="currentColor"
        strokeWidth={3}
        strokeLinecap="round"
        strokeDasharray={segments(72, 2, 0.28)}
      />

      {/* Segmented coil ring */}
      <g className="r-spin r-coils">
        <circle
          cx={CENTER}
          cy={CENTER}
          r={60}
          fill="none"
          stroke="currentColor"
          strokeWidth={10}
          strokeDasharray={segments(60, 10, 0.78)}
          opacity={0.22}
        />
        <circle
          cx={CENTER}
          cy={CENTER}
          r={60}
          fill="none"
          stroke="currentColor"
          strokeWidth={1}
          strokeDasharray={segments(60, 10, 0.78)}
          opacity={0.85}
        />
      </g>

      <circle cx={CENTER} cy={CENTER} r={47} fill="none" stroke="currentColor" strokeWidth={0.8} opacity={0.7} />
      <circle
        className="r-spin r-inner"
        cx={CENTER}
        cy={CENTER}
        r={42}
        fill="none"
        stroke="currentColor"
        strokeWidth={1.2}
        strokeDasharray="1 3"
        opacity={0.55}
      />

      {/* Glowing core */}
      <g className="r-core">
        <circle cx={CENTER} cy={CENTER} r={34} fill={`url(#${gradientId})`} />
        <circle cx={CENTER} cy={CENTER} r={22} fill="none" stroke="#e8fdff" strokeWidth={1.2} opacity={0.8} />
      </g>
    </svg>
  );
}
