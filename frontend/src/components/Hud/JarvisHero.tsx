import { useNavigate } from 'react-router';
import { Database, MessageSquare } from 'lucide-react';
import { useAppStore } from '../../lib/store';
import { ArcReactor } from './ArcReactor';
import { useJarvisActivity } from './useJarvisActivity';

function getGreeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return 'Good morning';
  if (hour < 18) return 'Good afternoon';
  return 'Good evening';
}

function Readout({ label, value }: { label: string; value: string }) {
  return (
    <div className="hud-stat">
      <span className="hud-stat-label">{label}</span>
      <span className="hud-stat-value" title={value}>{value}</span>
    </div>
  );
}

/** Empty-chat landing: the reactor core, greeting and system readouts. */
export function JarvisHero() {
  const navigate = useNavigate();
  const activity = useJarvisActivity();
  const selectedModel = useAppStore((s) => s.selectedModel);
  const serverInfo = useAppStore((s) => s.serverInfo);
  const voiceOn = useAppStore((s) => s.settings.voiceOutputEnabled);

  const openMessaging = () => {
    navigate('/data-sources');
    setTimeout(() => window.dispatchEvent(new CustomEvent('switch-tab', { detail: 'messaging' })), 100);
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-full px-4 py-6 text-center">
      <ArcReactor size="clamp(120px, 26vh, 240px)" state={activity} className="mb-5" />

      <div className="hud-wordmark hud-wordmark-lg">J.A.R.V.I.S.</div>
      <div className="hud-readout mt-1.5 mb-5 hud-tagline">Just A Rather Very Intelligent System</div>

      <h2 className="hud-greeting">
        {getGreeting()}.
        <span className="hud-caret" aria-hidden="true" />
      </h2>
      <p className="text-sm max-w-md mt-1.5 mb-5" style={{ color: 'var(--color-text-secondary)' }}>
        All systems online. Running locally on this Mac — private, fast, and always available.
      </p>

      <div className="hud-stat-strip mb-5">
        <Readout label="Core" value={selectedModel || serverInfo?.model || 'Standby'} />
        <Readout label="Engine" value={serverInfo?.engine || 'Local'} />
        <Readout label="Voice" value={voiceOn ? 'On' : 'Off'} />
      </div>

      <div className="flex flex-wrap justify-center gap-3">
        <button onClick={() => navigate('/data-sources')} className="hud-chip">
          <Database size={14} />
          Connect Data Sources
        </button>
        <button onClick={openMessaging} className="hud-chip">
          <MessageSquare size={14} />
          Messaging Channels
        </button>
      </div>
    </div>
  );
}
