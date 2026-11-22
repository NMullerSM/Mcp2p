import React from 'react';
import { RestoreSessionBanner } from './RestoreSessionBanner';
import { useAutoMeasure } from './useAutoMeasure';

/**
 * Región superior de la matriz de terminales: banner de restauración de sesiones (WP-4b),
 * encima de los banners y la cuadrícula. El chat del humano ya no vive aquí: es el módulo
 * global de chat (src/chat), montado una vez desde App.
 */
export function MatrixTopRegion({ api, projectUuid, onRestored }) {
  useAutoMeasure(api, projectUuid);
  return (
    <div
      data-testid="matrix-top-region"
      style={{ display: 'flex', flexDirection: 'column', gap: '8px', flexShrink: 0 }}
    >
      <RestoreSessionBanner api={api} projectUuid={projectUuid} onRestored={onRestored} />
    </div>
  );
}
