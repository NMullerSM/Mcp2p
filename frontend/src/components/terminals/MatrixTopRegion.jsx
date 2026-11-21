import React from 'react';
import { RestoreSessionBanner } from './RestoreSessionBanner';
import { HumanChatPanel } from '../relay/HumanChatPanel';
import { useAutoMeasure } from './useAutoMeasure';

/**
 * Región superior de la matriz de terminales: banner de restauración de sesiones (WP-4b)
 * seguido del panel de chat del humano y permisos (WP-8), encima de los banners y la cuadrícula.
 */
export function MatrixTopRegion({
  api,
  projectUuid,
  onRestored,
  panes,
  catalogById,
  relay,
  onOpenSettings
}) {
  useAutoMeasure(api, projectUuid);
  return (
    <div
      data-testid="matrix-top-region"
      style={{ display: 'flex', flexDirection: 'column', gap: '8px', flexShrink: 0 }}
    >
      <RestoreSessionBanner api={api} projectUuid={projectUuid} onRestored={onRestored} />
      <HumanChatPanel
        api={api}
        projectUuid={projectUuid}
        panes={panes}
        catalogById={catalogById}
        relay={relay}
        onOpenSettings={onOpenSettings}
      />
    </div>
  );
}
