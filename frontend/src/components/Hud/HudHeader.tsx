import { useEffect, useState } from 'react';
import { PanelRightOpen, PanelRightClose } from 'lucide-react';
import { useAppStore } from '../../lib/store';
import { ArcReactor } from './ArcReactor';
import { useJarvisActivity } from './useJarvisActivity';

const STATUS_LABEL = {
  idle: 'Online',
  thinking: 'Processing',
  speaking: 'Speaking',
} as const;

/** Ticks once a second; isolated so the rest of the header doesn't re-render. */
function HudClock() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const time = now.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  });
  const date = now.toLocaleDateString([], { weekday: 'short', day: '2-digit', month: 'short' });

  return (
    <div className="hud-readout flex items-baseline gap-2">
      <span className="hud-clock">{time}</span>
      <span className="hidden sm:inline">{date}</span>
    </div>
  );
}

/** Top bar of the chat view: live status, active model, clock, panel toggle. */
export function HudHeader() {
  const sidebarOpen = useAppStore((s) => s.sidebarOpen);
  const systemPanelOpen = useAppStore((s) => s.systemPanelOpen);
  const toggleSystemPanel = useAppStore((s) => s.toggleSystemPanel);
  const selectedModel = useAppStore((s) => s.selectedModel);
  const serverModel = useAppStore((s) => s.serverInfo?.model);
  const activity = useJarvisActivity();

  const PanelIcon = systemPanelOpen ? PanelRightClose : PanelRightOpen;
  const model = selectedModel || serverModel;
  const shortcut = navigator.platform.includes('Mac') ? '⌘' : 'Ctrl';

  return (
    // Leave room for the fixed buttons that overlay the window corners: "open
    // sidebar" top-left while the sidebar is closed, and the approvals bell
    // top-right unless the system panel sits between it and this header.
    <div
      className={`hud-header flex items-center gap-3 py-1.5 shrink-0 ${sidebarOpen ? 'pl-4' : 'pl-14'} ${
        systemPanelOpen ? 'pr-3' : 'pr-14'
      }`}
    >
      <div className="flex items-center gap-2.5 min-w-0">
        <ArcReactor size={22} state={activity} />
        <span className="hud-status" data-state={activity}>
          <span className="hud-status-dot" />
          {STATUS_LABEL[activity]}
        </span>
        {model && (
          <span className="hud-readout truncate hidden md:inline">
            Core <span style={{ color: 'var(--color-text-secondary)' }}>{model}</span>
          </span>
        )}
      </div>
      <div className="ml-auto flex items-center gap-3">
        <HudClock />
        <button
          onClick={toggleSystemPanel}
          className="hud-icon-button"
          title={`${systemPanelOpen ? 'Hide' : 'Show'} system panel (${shortcut}+I)`}
        >
          <PanelIcon size={16} />
        </button>
      </div>
    </div>
  );
}
