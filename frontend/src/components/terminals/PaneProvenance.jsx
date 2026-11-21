import React from 'react';
import { shortId } from '../relay/relayModel';

const base = {
  fontFamily: 'var(--font-mono)',
  fontSize: '9.5px',
  fontWeight: 600,
  lineHeight: 1.2,
  padding: '1px 5px',
  borderRadius: '4px',
  whiteSpace: 'nowrap'
};

/**
 * Franja de procedencia de un panel: identidad estable (node_uuid corto, la misma que ve el destinatario en el
 * sobre firmado) y estado del relevo: pausa. Solo informa; no autoriza ninguna acción.
 */
export function PaneProvenance({ paneId, nodeUuid, relayPaused = false, globalPaused = false }) {
  if (!nodeUuid && !relayPaused && !globalPaused) return null;
  return (
    <span
      data-testid={`pane-provenance-${paneId}`}
      style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', minWidth: 0, pointerEvents: 'auto' }}
    >
      {nodeUuid && (
        <span
          data-testid={`pane-provenance-node-${paneId}`}
          title={`Identidad estable de esta terminal en este proyecto: ${nodeUuid}. Es la que firma el Hub en sus mensajes.`}
          style={{ ...base, color: 'var(--text-secondary)', border: '1px solid var(--border-subtle)', backgroundColor: 'var(--bg-subtle)' }}
        >
          {shortId(nodeUuid, 12)}
        </span>
      )}
      {(relayPaused || globalPaused) && (
        <span
          data-testid={`pane-provenance-paused-${paneId}`}
          title={
            globalPaused
              ? 'Parada global del relevo: ningún agente recibe ni envía mensajes.'
              : 'Este agente puso en pausa su propio relevo; solo usted puede reanudarlo.'
          }
          style={{ ...base, color: '#fbbf24', backgroundColor: 'rgba(245, 158, 11, 0.14)', border: '1px solid rgba(245, 158, 11, 0.35)' }}
        >
          {globalPaused ? 'Relevo detenido' : 'Relevo en pausa'}
        </span>
      )}
    </span>
  );
}
