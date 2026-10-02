import { useAppStore } from '../../lib/store';
import { useTtsStore } from '../../lib/tts';
import type { ReactorState } from './ArcReactor';

/** What the assistant is doing right now, for HUD indicators. */
export function useJarvisActivity(): ReactorState {
  const isStreaming = useAppStore((s) => s.streamState.isStreaming);
  const ttsState = useTtsStore((s) => s.state);
  if (ttsState === 'speaking') return 'speaking';
  if (isStreaming || ttsState === 'loading') return 'thinking';
  return 'idle';
}
