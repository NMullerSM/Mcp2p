import { useCallback, useEffect, useRef, useState } from 'react';
import { describeRelayError } from './relayModel';

export const RELAY_POLL_MS = 5000;

const EMPTY = Object.freeze({
  available: false,
  loading: false,
  inbox: { items: [], error: null },
  audit: null,
  auditError: null,
  globalPaused: false,
  pausedPanes: []
});

/**
 * Estado del relevo que muestra la UI: bandeja del humano (mensajes retenidos), auditoria y paradas.
 * Solo lee del puente; si el puente no tiene los metodos (version antigua o pruebas) no sondea.
 * `enabled=false` (vista oculta) pausa el sondeo.
 */
export function useRelayState(api, projectUuid, { enabled = true, intervalMs = RELAY_POLL_MS } = {}) {
  const available = Boolean(api?.relay_audit_status || api?.relay_list_human_inbox);
  const [state, setState] = useState(() => ({ ...EMPTY, available, loading: available }));
  const mountedRef = useRef(true);
  const seqRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!available) return;
    const seq = ++seqRef.current;
    const [inboxRes, auditRes] = await Promise.all([
      api.relay_list_human_inbox
        ? Promise.resolve(api.relay_list_human_inbox(projectUuid || undefined)).catch((err) => err)
        : Promise.resolve(null),
      api.relay_audit_status ? Promise.resolve(api.relay_audit_status()).catch((err) => err) : Promise.resolve(null)
    ]);
    if (!mountedRef.current || seq !== seqRef.current) return;
    setState((prev) => {
      const next = { ...prev, available: true, loading: false };
      if (inboxRes) {
        const failed = inboxRes instanceof Error || inboxRes.success === false;
        next.inbox = failed
          ? { items: prev.inbox.items, error: describeRelayError(inboxRes, 'leer la bandeja del humano') }
          : { items: Array.isArray(inboxRes.items) ? inboxRes.items : [], error: null };
      }
      if (auditRes) {
        const failed = auditRes instanceof Error || auditRes.success === false;
        if (failed) {
          next.auditError = describeRelayError(auditRes, 'leer el estado de la auditoría');
        } else {
          next.audit = auditRes;
          next.auditError = null;
          next.globalPaused = Boolean(auditRes.global_paused);
          next.pausedPanes = Array.isArray(auditRes.paused_panes) ? auditRes.paused_panes : [];
        }
      }
      return next;
    });
  }, [api, available, projectUuid]);

  useEffect(() => {
    if (!available || !enabled) return undefined;
    void refresh();
    const timer = setInterval(() => void refresh(), intervalMs);
    return () => clearInterval(timer);
  }, [available, enabled, intervalMs, refresh]);

  return { ...state, available, refresh };
}
