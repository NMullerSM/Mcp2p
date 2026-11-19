import { useCallback, useEffect, useRef, useState } from 'react';
import { describeTerminalError } from './terminalModel';

/**
 * Flujo de instalación en dos pasos (preparar plan -> confirmar con token de un solo uso).
 */
export function useInstallFlow({
  api,
  mountedRef,
  catalogRef,
  setNotice,
  showNotice,
  runWithNativeDialogNotice,
  markPaneOpened,
  requestTerminalFocus,
  closeSlider,
  refreshMatrixStatus
}) {
  // Plan de instalacion pendiente de confirmar. El token de un solo uso vive SOLO en
  // installTokenRef (memoria de este hook): no se pinta, no se guarda, no se registra.
  const [installPlan, setInstallPlan] = useState(null);
  const [installBusyCliId, setInstallBusyCliId] = useState(null);
  const installTokenRef = useRef(null);
  const installingRef = useRef(false);

  useEffect(() => () => {
    installTokenRef.current = null;
  }, []);

  const discardInstallPlan = useCallback(() => {
    installTokenRef.current = null;
    setInstallPlan(null);
  }, []);

  const handlePrepareInstall = useCallback(async (cliId) => {
    if (!api?.pty_prepare_install || installingRef.current) return;
    const cli = catalogRef.current.clis.find((c) => c.id === cliId);
    const context = { cliId, cliName: cli?.display_name };
    // Preparar otra instalacion descarta la confirmacion anterior.
    discardInstallPlan();
    installingRef.current = true;
    setInstallBusyCliId(cliId);
    try {
      const res = await api.pty_prepare_install(cliId);
      if (!mountedRef.current) return;
      if (!res?.success || !res.requires_confirmation || !res.confirmation_token || !res.command) {
        showNotice(res && res.success === false ? res : { code: 'INSTALL_NOT_AVAILABLE', message: 'El backend no devolvió un comando para confirmar.' }, context);
        return;
      }
      // Se muestra tal cual lo devuelve el backend; el frontend nunca arma ni edita el comando.
      installTokenRef.current = res.confirmation_token;
      setNotice(null);
      setInstallPlan({
        cliId,
        displayName: res.display_name || cli?.display_name || cliId,
        command: res.command,
        method: res.method || '',
        downloadsScript: Boolean(res.downloads_script),
        warning: res.warning || '',
        requires: Array.isArray(res.requires) ? res.requires.filter((r) => typeof r === 'string') : [],
        platform: res.platform || '',
        sourceUrl: res.source_url || '',
        note: res.note || '',
        expiresInS: Number(res.expires_in_s) > 0 ? Number(res.expires_in_s) : 120
      });
    } catch (err) {
      if (mountedRef.current) showNotice({ code: 'INTERNAL_ERROR', message: err?.message || String(err) }, context);
    } finally {
      installingRef.current = false;
      if (mountedRef.current) setInstallBusyCliId(null);
    }
  }, [api, mountedRef, catalogRef, setNotice, showNotice, discardInstallPlan]);

  const handleConfirmInstall = useCallback(async () => {
    const plan = installPlan;
    const token = installTokenRef.current;
    if (!plan || !token || !api?.pty_install_cli || installingRef.current) return;
    // El token es de un solo uso: se suelta antes de llamar, pase lo que pase.
    discardInstallPlan();
    const context = { cliId: plan.cliId, cliName: plan.displayName };
    installingRef.current = true;
    setInstallBusyCliId(plan.cliId);
    try {
      const res = await runWithNativeDialogNotice(() => api.pty_install_cli(plan.cliId, token));
      if (!mountedRef.current) return;
      if (!res?.success || !res.pane_id) {
        showNotice(res || { code: 'INTERNAL_ERROR', message: 'No se pudo iniciar la instalación.' }, context);
        return;
      }
      markPaneOpened(res.pane_id, {
        kind: 'install',
        cliProfile: 'custom_shell',
        role: 'installer',
        cwd: null,
        pid: res.pid ?? null,
        sessionId: res.session_id || ''
      });
      setNotice(null);
      requestTerminalFocus(res.pane_id);
      closeSlider();
      void refreshMatrixStatus();
    } catch (err) {
      if (mountedRef.current) showNotice({ code: 'INTERNAL_ERROR', message: err?.message || String(err) }, context);
    } finally {
      installingRef.current = false;
      if (mountedRef.current) setInstallBusyCliId(null);
    }
  }, [api, mountedRef, installPlan, discardInstallPlan, runWithNativeDialogNotice, showNotice, setNotice, markPaneOpened, requestTerminalFocus, closeSlider, refreshMatrixStatus]);

  // La confirmacion vence (el backend la invalida a los 120 s); se retira tambien de la pantalla.
  useEffect(() => {
    if (!installPlan) return undefined;
    const timer = setTimeout(() => {
      installTokenRef.current = null;
      setInstallPlan(null);
      setNotice(describeTerminalError({ code: 'INSTALL_TOKEN_EXPIRED' }));
    }, installPlan.expiresInS * 1000);
    return () => clearTimeout(timer);
  }, [installPlan, setNotice]);

  return {
    installPlan,
    installBusyCliId,
    discardInstallPlan,
    handlePrepareInstall,
    handleConfirmInstall
  };
}
