import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppConfirmDialog } from './AppConfirmDialog';

/**
 * Envuelve llamadas al puente que pueden responder `CONFIRMATION_REQUIRED`:
 * abre `AppConfirmDialog` con los textos de Python y envía `ui_confirm_action`.
 */
export function useAppConfirmation(api) {
  const [pending, setPending] = useState(null);
  const [busy, setBusy] = useState(false);
  const pendingRef = useRef(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (pendingRef.current) {
        const { resolve } = pendingRef.current;
        pendingRef.current = null;
        resolve({
          success: false,
          code: 'CONFIRMATION_DECLINED',
          message: 'La acción fue cancelada al cerrar la vista.'
        });
      }
    };
  }, []);

  const handleCancel = useCallback(async () => {
    const current = pendingRef.current;
    if (!current) return;
    pendingRef.current = null;
    if (mountedRef.current) {
      setPending(null);
      setBusy(false);
    }
    let declinedRes = null;
    try {
      if (typeof api?.ui_confirm_action === 'function') {
        declinedRes = await api.ui_confirm_action(current.confirmation.token, false, false);
      }
    } catch {
      // Si falla la notificación de rechazo, devolvemos igualmente CONFIRMATION_DECLINED local.
    }
    current.resolve(
      declinedRes && declinedRes.success === false
        ? declinedRes
        : {
            success: false,
            code: 'CONFIRMATION_DECLINED',
            message: 'La acción fue rechazada en el diálogo de confirmación.'
          }
    );
  }, [api]);

  const handleConfirm = useCallback(
    async (remember) => {
      const current = pendingRef.current;
      if (!current) return;
      if (mountedRef.current) setBusy(true);
      try {
        const res = await api.ui_confirm_action(
          current.confirmation.token,
          true,
          Boolean(current.confirmation.rememberable && remember)
        );
        pendingRef.current = null;
        if (mountedRef.current) {
          setPending(null);
          setBusy(false);
        }
        current.resolve(res);
      } catch (err) {
        pendingRef.current = null;
        if (mountedRef.current) {
          setPending(null);
          setBusy(false);
        }
        current.resolve({
          success: false,
          code: 'INTERNAL_ERROR',
          message: err?.message || String(err)
        });
      }
    },
    [api]
  );

  const run = useCallback(
    async (fn) => {
      const res = await fn();
      if (res?.code !== 'CONFIRMATION_REQUIRED' || !res?.confirmation?.token) {
        return res;
      }
      if (typeof api?.ui_confirm_action !== 'function') {
        return {
          success: false,
          code: 'CONFIRMATION_UNAVAILABLE',
          message: 'Esta versión no admite confirmaciones propias.'
        };
      }
      if (pendingRef.current) {
        const prev = pendingRef.current;
        pendingRef.current = null;
        prev.resolve({
          success: false,
          code: 'CONFIRMATION_DECLINED',
          message: 'Sustituida por una nueva confirmación.'
        });
      }
      return new Promise((resolve) => {
        const next = { confirmation: res.confirmation, resolve };
        pendingRef.current = next;
        if (mountedRef.current) {
          setBusy(false);
          setPending(next);
        }
      });
    },
    [api]
  );

  const dialog = React.createElement(AppConfirmDialog, {
    confirmation: pending?.confirmation || null,
    busy,
    onConfirm: handleConfirm,
    onCancel: handleCancel
  });

  return { dialog, run };
}
