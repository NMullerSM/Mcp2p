import { useCallback, useEffect, useRef, useState } from 'react';
import { useAppConfirmation } from '../common/useAppConfirmation';
import { describeRelayError } from '../relay/relayModel';

// El contrato admite la confirmacion plana (token/title/message en la raiz); useAppConfirmation espera
// `confirmation: { token, ... }`, que es la forma que ya usa el resto del puente. Se normaliza aqui.
function withConfirmation(res) {
  if (res?.code === 'CONFIRMATION_REQUIRED' && !res.confirmation && res.token) {
    return {
      ...res,
      confirmation: {
        token: res.token,
        title: res.title,
        message: res.message,
        rememberable: Boolean(res.rememberable)
      }
    };
  }
  return res;
}

/**
 * Acciones de entrega directa sobre el puente: modo por terminal y lote.
 * Activar «direct» puede pedir la confirmacion propia de la app (`run` la abre); el llamador debe
 * renderizar `dialog`. `onChanged({ kind, paneId, ... , projectUuid })` avisa tras un cambio aceptado.
 * Rechazar el dialogo no es un error: no muestra mensaje.
 */
export function useDeliveryActions({ api, projectUuid = '', onChanged } = {}) {
  const { dialog, run } = useAppConfirmation(api);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const mountedRef = useRef(true);
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const perform = useCallback(async (fn, action, onSuccess) => {
    setBusy(true);
    setError(null);
    try {
      const res = await run(() => Promise.resolve(fn()).then(withConfirmation));
      if (!mountedRef.current) return res;
      if (!res || res.success === false) {
        if (res?.code !== 'CONFIRMATION_DECLINED') {
          setError(describeRelayError(res || {}, action));
        }
        return res || { success: false };
      }
      onSuccess(res);
      return res;
    } catch (err) {
      if (mountedRef.current) setError(describeRelayError(err, action));
      return { success: false, code: 'INTERNAL_ERROR', message: err?.message || String(err) };
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  }, [run]);

  const setMode = useCallback((paneId, mode) => {
    if (typeof api?.pty_set_delivery_mode !== 'function') {
      setError('Esta versión no admite cambiar la entrega directa.');
      return Promise.resolve({ success: false, code: 'UNAVAILABLE' });
    }
    return perform(
      () => api.pty_set_delivery_mode(paneId, mode),
      'cambiar la entrega directa',
      () => onChangedRef.current?.({ kind: 'mode', paneId, mode, projectUuid })
    );
  }, [api, perform, projectUuid]);

  const setBatch = useCallback((paneId, enabled) => {
    if (typeof api?.pty_set_delivery_batch !== 'function') {
      setError('Esta versión no admite el envío por lotes.');
      return Promise.resolve({ success: false, code: 'UNAVAILABLE' });
    }
    return perform(
      () => api.pty_set_delivery_batch(paneId, Boolean(enabled)),
      'cambiar el envío por lotes',
      () => onChangedRef.current?.({ kind: 'batch', paneId, enabled: Boolean(enabled), projectUuid })
    );
  }, [api, perform, projectUuid]);

  return { setMode, setBatch, busy, error, dialog };
}
