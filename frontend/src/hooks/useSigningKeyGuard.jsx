import React, { useState, useRef, useCallback, useEffect } from 'react';
import { UnlockSigningKeyDialog } from '../components/UnlockSigningKeyDialog';

/**
 * ¿La respuesta del backend dice que la clave de firma humana está bloqueada? (Gate 0c / EGM-53)
 *
 * Los adaptadores HITL devuelven `code: "KEY_LOCKED"`. Algunos flujos (invitaciones, unión P2P)
 * capturan la excepción con un `except Exception` genérico y solo dejan `KEY_LOCKED: ...` en
 * `error`, así que también se reconoce ese marcador textual.
 */
export function isKeyLocked(res) {
  if (!res || typeof res !== 'object' || res.success === true) return false;
  if (res.code === 'KEY_LOCKED') return true;
  return typeof res.error === 'string' && /\bKEY_LOCKED\b/.test(res.error);
}

/**
 * Hook reutilizable para toda acción de la UI que firma con la clave humana.
 *
 *   const { withKeyUnlock, unlockDialog } = useSigningKeyGuard(api);
 *   const res = await withKeyUnlock(() => api.sign_and_approve_item(...));
 *   ...
 *   return (<>... {unlockDialog}</>);
 *
 * `withKeyUnlock(action)` ejecuta `action()` (una función que devuelve la respuesta de la API).
 * Si trae `KEY_LOCKED`, abre un diálogo que pide la frase, llama a `api.unlock_signing_key` y,
 * si desbloquea, REINTENTA `action()` una sola vez y devuelve ese resultado. `BAD_PASSPHRASE`
 * se muestra en el propio diálogo sin cerrarlo. Si el humano cancela, resuelve con
 * `{ success: false, cancelled: true, code: 'KEY_LOCKED', error }` para que el llamador
 * no muestre un error ruidoso (`res.cancelled`).
 *
 * La frase solo existe en el estado local del diálogo; este hook no la ve ni la guarda.
 */
export function useSigningKeyGuard(api) {
  const [pending, setPending] = useState(null);
  const pendingRef = useRef(null);
  const apiRef = useRef(api);
  apiRef.current = api;

  const settle = useCallback((result) => {
    const current = pendingRef.current;
    if (!current) return;
    pendingRef.current = null;
    setPending(null);
    current.resolve(result);
  }, []);

  const cancelledResult = (firstResult) => ({
    success: false,
    cancelled: true,
    code: 'KEY_LOCKED',
    error: (firstResult && firstResult.error) || 'KEY_LOCKED: la clave de firma está bloqueada.'
  });

  const withKeyUnlock = useCallback(async (action) => {
    const first = await action();
    if (!isKeyLocked(first)) return first;
    // Un único diálogo a la vez: si ya hay uno abierto, esta llamada devuelve el KEY_LOCKED original.
    if (pendingRef.current) return first;
    return new Promise((resolve) => {
      const entry = { action, resolve, first };
      pendingRef.current = entry;
      setPending(entry);
    });
  }, []);

  const handleSubmit = useCallback(async (passphrase) => {
    const current = pendingRef.current;
    const bridge = apiRef.current;
    if (!current) return { ok: true };
    if (!bridge || typeof bridge.unlock_signing_key !== 'function') {
      return { ok: false, error: 'El puente de la aplicación no expone el desbloqueo de la clave.' };
    }
    let res;
    try {
      res = await bridge.unlock_signing_key(passphrase);
    } catch (err) {
      return { ok: false, error: 'Error de comunicación al desbloquear la clave.' };
    }
    if (!res || !res.success) {
      return { ok: false, error: (res && res.error) || 'No se pudo desbloquear la clave.' };
    }
    // Desbloqueada: se cierra el diálogo y se reintenta la acción original UNA vez.
    pendingRef.current = null;
    setPending(null);
    let retried;
    try {
      retried = await current.action();
    } catch (err) {
      current.resolve({ success: false, error: String(err) });
      return { ok: true };
    }
    current.resolve(retried);
    return { ok: true };
  }, []);

  const handleCancel = useCallback(() => {
    const current = pendingRef.current;
    settle(cancelledResult(current && current.first));
  }, [settle]);

  // Si la vista se desmonta con un diálogo abierto, no deja la promesa colgada.
  useEffect(() => () => {
    const current = pendingRef.current;
    if (current) {
      pendingRef.current = null;
      current.resolve(cancelledResult(current.first));
    }
  }, []);

  const unlockDialog = pending ? (
    <UnlockSigningKeyDialog isOpen={true} onSubmit={handleSubmit} onCancel={handleCancel} />
  ) : null;

  return { withKeyUnlock, unlockDialog };
}
