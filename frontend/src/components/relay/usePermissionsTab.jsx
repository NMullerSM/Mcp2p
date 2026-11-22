import React, { useMemo, useRef } from 'react';
import { PermissionsTab } from './PermissionsTab';
import { useRelayState } from './useRelayState';

/**
 * Pestaña «Permisos» para el chat global: la compone la aplicación (el modulo de chat solo recibe `extraTabs`).
 * Devuelve `[]` sin proyecto activo o si el puente no tiene el relevo; si no, una sola pestaña cuya insignia es
 * el número de mensajes retenidos.
 *
 * El array es estable mientras no cambien el puente, el proyecto o la insignia: `relay` cambia en cada sondeo
 * y, si entrara en las dependencias, el dock se repintaría cada 5 s. Por eso el `render` lee el ultimo `relay`
 * desde una referencia (ChatDock lo llama al pintar, después de que la app haya renderizado con el dato nuevo).
 */
export function usePermissionsTab(api, projectUuid) {
  const relay = useRelayState(api, projectUuid, { enabled: Boolean(projectUuid) });
  const relayRef = useRef(relay);
  relayRef.current = relay;

  const available = Boolean(relay.available);
  const badge = Array.isArray(relay.inbox?.items) ? relay.inbox.items.length : 0;

  return useMemo(() => {
    if (!projectUuid || !available) return [];
    return [
      {
        id: 'permisos',
        label: 'Permisos',
        badge,
        render: () => <PermissionsTab api={api} relay={relayRef.current} />
      }
    ];
  }, [api, projectUuid, available, badge]);
}
