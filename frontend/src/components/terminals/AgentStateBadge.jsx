import React from 'react';

const STATE_SPECS = {
  idle: {
    label: 'Libre',
    color: '#34d399',
    bg: 'rgba(16, 185, 129, 0.14)',
    border: 'rgba(16, 185, 129, 0.35)'
  },
  working: {
    label: 'Trabajando',
    color: '#60a5fa',
    bg: 'rgba(59, 130, 246, 0.14)',
    border: 'rgba(59, 130, 246, 0.35)'
  },
  permission: {
    label: 'Esperando permiso',
    color: '#fbbf24',
    bg: 'rgba(245, 158, 11, 0.14)',
    border: 'rgba(245, 158, 11, 0.35)'
  }
};

/**
 * Insignia de estado del agente reportado exclusivamente por hooks del CLI.
 * No se muestra si `source !== 'hook'` o si el estado es `unknown` / `exited`.
 */
export function AgentStateBadge({ pane }) {
  const agentState = pane?.agentState || pane?.agent_state;
  if (!agentState || agentState.source !== 'hook') return null;

  const spec = STATE_SPECS[agentState.state];
  if (!spec) return null;

  const paneId = pane?.paneId || pane?.pane_id || 'unknown';
  return (
    <span
      data-testid={`agent-state-badge-${paneId}`}
      data-agent-state={agentState.state}
      data-agent-source={agentState.source}
      title={`Estado del agente: ${spec.label} (según los hooks del CLI)`}
      style={{
        fontFamily: 'var(--font-mono)',
        fontSize: '9.5px',
        fontWeight: 600,
        lineHeight: 1.2,
        padding: '1px 5px',
        borderRadius: '4px',
        color: spec.color,
        backgroundColor: spec.bg,
        border: `1px solid ${spec.border}`,
        whiteSpace: 'nowrap',
        pointerEvents: 'auto'
      }}
    >
      {spec.label}
    </span>
  );
}
