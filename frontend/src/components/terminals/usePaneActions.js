import { useCallback, useMemo, useRef, useState } from 'react';
import { createInputSender } from './inputSender';
import {
  acquireTerminal,
  disposeTerminal,
  getTerminalEntry
} from './terminalRegistry';
import {
  DEFAULT_PTY_SIZE,
  MATRIX_PANE_IDS,
  describeTerminalError,
  forgetPaneSession,
  pickFreePaneId,
  rememberPaneSession
} from './terminalModel';

function parseTrippedCase(rawCase, fallbackProjectUuid = '') {
  if (rawCase && typeof rawCase === 'object') {
    return {
      label: rawCase.card_id || rawCase.session_id || rawCase.case_id || JSON.stringify(rawCase),
      projectUuid: rawCase.project_uuid || fallbackProjectUuid || '',
      cardId: rawCase.card_id || '',
      sessionId: rawCase.session_id || ''
    };
  }
  const text = String(rawCase || '').trim();
  if (!text) {
    return { label: '', projectUuid: fallbackProjectUuid || '', cardId: '', sessionId: '' };
  }
  if (text.startsWith('card:')) {
    return { label: text, projectUuid: fallbackProjectUuid || '', cardId: text.slice(5), sessionId: '' };
  }
  if (text.startsWith('session:')) {
    return { label: text, projectUuid: fallbackProjectUuid || '', cardId: '', sessionId: text.slice(8) };
  }
  const parts = text.split(':');
  if (parts.length >= 3) {
    return {
      label: text,
      projectUuid: parts[0] || fallbackProjectUuid || '',
      cardId: parts[1] || '',
      sessionId: parts[2] || ''
    };
  }
  if (parts.length === 2) {
    return {
      label: text,
      projectUuid: parts[0] || fallbackProjectUuid || '',
      cardId: parts[1] || '',
      sessionId: ''
    };
  }
  return { label: text, projectUuid: fallbackProjectUuid || '', cardId: text, sessionId: '' };
}

/**
 * Acciones imperativas sobre paneles y controles de la matriz:
 * lanzar, cerrar, pausar, redimensionar, envío de teclas, Auto-Relay,
 * modo trabajador, registro de agy y reinicio del Loop Breaker.
 */
export function usePaneActions({
  api,
  apiRef,
  project,
  panesRef,
  setPanes,
  createInitialPaneState,
  catalogRef,
  setCatalog,
  matrixStatus,
  setMatrixStatus,
  mountedRef,
  pollersRef,
  showNotice,
  setNotice,
  runWithNativeDialogNotice,
  requestTerminalFocus,
  closeSlider,
  refreshCatalog,
  refreshMatrixStatus,
  cwdInput,
  roleInput
}) {
  const [launchingCliId, setLaunchingCliId] = useState(null);
  const [unregisterAgyBusy, setUnregisterAgyBusy] = useState(false);
  const [resettingBreaker, setResettingBreaker] = useState(false);

  // Un emisor de entrada por panel: una sola llamada pty_write_input en vuelo y lo tecleado
  // mientras tanto sale agrupado en la siguiente, en orden (EGM-80: cada llamada del puente
  // corre en su propio hilo de Python y dos solapadas pueden llegar al PTY al reves).
  const inputSendersRef = useRef({});
  const launchingRef = useRef(false);
  const showNoticeRef = useRef(showNotice);
  showNoticeRef.current = showNotice;

  const patchPane = useCallback((paneId, patch) => {
    setPanes((prev) => (prev[paneId] ? { ...prev, [paneId]: { ...prev[paneId], ...patch } } : prev));
  }, [setPanes]);

  const markPaneOpened = useCallback((paneId, patch) => {
    // Sesion nueva en este panel: terminal limpia, lectura desde el principio y sin entrada pendiente.
    inputSendersRef.current[paneId]?.clear();
    delete inputSendersRef.current[paneId];
    disposeTerminal(paneId);
    const entry = acquireTerminal(paneId, {
      sessionId: patch.sessionId || '',
      cols: patch.cols || DEFAULT_PTY_SIZE.cols,
      rows: patch.rows || DEFAULT_PTY_SIZE.rows
    });
    entry.lastActivityAt = Date.now();
    setPanes((prev) => ({
      ...prev,
      [paneId]: { ...createInitialPaneState(paneId), open: true, alive: true, ...patch }
    }));
    rememberPaneSession(paneId, { sessionId: patch.sessionId, cwd: patch.cwd ?? null, kind: patch.kind || 'cli' });
  }, [createInitialPaneState, setPanes]);

  const handleLaunchCli = useCallback(async (cliId, customCommand = null) => {
    if (!api?.pty_spawn_pane || launchingRef.current) return;
    const cli = catalogRef.current.clis.find((c) => c.id === cliId);
    const context = { cliId, cliName: cli?.display_name };
    const currentOpen = MATRIX_PANE_IDS.filter((id) => panesRef.current[id].open);
    const paneId = pickFreePaneId(currentOpen, catalogRef.current.maxPanes);
    if (!paneId) {
      showNotice({ code: 'PANE_LIMIT_REACHED', reason: 'max_panes', max_panes: catalogRef.current.maxPanes }, context);
      return;
    }

    launchingRef.current = true;
    setLaunchingCliId(cliId);
    try {
      const cwd = cwdInput.trim() || null;
      const command = typeof customCommand === 'string' && customCommand.trim() ? customCommand : null;
      const invoke = () =>
        api.pty_spawn_pane(
          paneId,
          cliId,
          roleInput,
          command,
          DEFAULT_PTY_SIZE.cols,
          DEFAULT_PTY_SIZE.rows,
          true,
          cwd,
          false,
          project?.project_uuid || null
        );
      const res = await (command ? runWithNativeDialogNotice(invoke) : invoke());
      if (!mountedRef.current) return;

      if (res?.hub_url) {
        setMatrixStatus((prev) => ({ ...prev, hubUrl: res.hub_url, hubRunning: true }));
      }

      if (!res?.success) {
        showNotice(res || { code: 'PTY_SPAWN_FAILED', message: 'No se pudo iniciar la sesión.' }, context);
        return;
      }

      const sess = res.session || {};
      const paneMeta = res.pane || {};
      const openedId = res.pane_id || paneId;
      markPaneOpened(openedId, {
        kind: 'cli',
        cliProfile: cliId,
        role: roleInput,
        cwd: res.cwd ?? cwd,
        cols: sess.cols || DEFAULT_PTY_SIZE.cols,
        rows: sess.rows || DEFAULT_PTY_SIZE.rows,
        paused: Boolean(sess.paused),
        pid: res.pid ?? sess.pid ?? paneMeta.pid ?? null,
        rssMb: typeof sess.rss_mb === 'number' ? sess.rss_mb : 0,
        nodeUuid: res.node_uuid || paneMeta.node_uuid || sess.node_uuid || '',
        sessionId: sess.session_id || '',
        registryStatus: paneMeta.status || '',
        mcpInjection: res.mcp_injection ?? sess.mcp_injection ?? paneMeta.mcp_injection ?? null,
        activityState: sess.activity_state ?? paneMeta.activity_state ?? null,
        activitySource: sess.activity_source ?? paneMeta.activity_source ?? '',
        projectUuid: res.project_uuid ?? paneMeta.project_uuid ?? project?.project_uuid ?? '',
        autoRelayEnabled: Boolean(paneMeta.auto_relay_enabled),
        workerMode: Boolean(paneMeta.worker_mode)
      });
      setNotice(null);
      requestTerminalFocus(openedId);
      closeSlider();
    } catch (err) {
      if (mountedRef.current) {
        showNotice({ code: 'PTY_SPAWN_FAILED', message: err?.message || String(err) }, context);
      }
    } finally {
      launchingRef.current = false;
      if (mountedRef.current) setLaunchingCliId(null);
    }
  }, [
    api,
    catalogRef,
    panesRef,
    cwdInput,
    roleInput,
    project?.project_uuid,
    mountedRef,
    setMatrixStatus,
    runWithNativeDialogNotice,
    showNotice,
    setNotice,
    markPaneOpened,
    requestTerminalFocus,
    closeSlider
  ]);

  const handlePausePane = useCallback(async (paneId) => {
    if (!api?.pty_pause_pane) return;
    const pane = panesRef.current[paneId];
    if (!pane || !pane.alive) return;
    const targetPaused = !pane.paused;
    try {
      const res = await api.pty_pause_pane(paneId, targetPaused);
      if (!mountedRef.current) return;
      if (res?.success) {
        setPanes((prev) => ({
          ...prev,
          [paneId]: { ...prev[paneId], paused: typeof res.paused === 'boolean' ? res.paused : targetPaused }
        }));
      } else if (res?.message || res?.error) {
        showNotice({ ...res, code: res.code || 'PTY_PAUSE_FAILED' });
      }
    } catch (err) {
      if (mountedRef.current) showNotice({ code: 'PTY_PAUSE_FAILED', message: err?.message || String(err) });
    }
  }, [api, panesRef, mountedRef, setPanes, showNotice]);

  const handleClosePane = useCallback(async (paneId) => {
    const pane = panesRef.current[paneId];
    if (!pane) return;
    try {
      if (api?.pty_close_pane && (pane.alive || pane.sessionId)) {
        await api.pty_close_pane(paneId);
      }
    } catch {
      // Limpiar estado visual aunque el proceso ya haya salido
    }
    inputSendersRef.current[paneId]?.clear();
    delete inputSendersRef.current[paneId];
    disposeTerminal(paneId);
    if (!mountedRef.current) return;
    forgetPaneSession(paneId);
    setPanes((prev) => ({ ...prev, [paneId]: createInitialPaneState(paneId) }));
  }, [api, panesRef, mountedRef, setPanes, createInitialPaneState]);

  const handleResizePane = useCallback(async (paneId, cols, rows) => {
    setPanes((prev) => ({ ...prev, [paneId]: { ...prev[paneId], cols, rows } }));
    const pane = panesRef.current[paneId];
    if (pane?.alive && api?.pty_resize_pane) {
      try {
        await api.pty_resize_pane(paneId, cols, rows);
      } catch {
        // Ignorar fallo transitorio de redimensionado
      }
    }
  }, [api, panesRef, setPanes]);

  const sendPaneInput = useCallback((paneId, payload) => {
    if (!api?.pty_write_input || !payload) return Promise.resolve();
    // Teclear = actividad: sondeo rapido para ver el eco enseguida.
    const entry = getTerminalEntry(paneId);
    if (entry) entry.lastActivityAt = Date.now();
    pollersRef.current[paneId]?.wake();
    let sender = inputSendersRef.current[paneId];
    if (!sender) {
      sender = createInputSender({
        send: (chunk) => apiRef.current.pty_write_input(paneId, chunk, true),
        onResult: (res) => {
          if (mountedRef.current && res && res.success === false && (res.message || res.error)) {
            showNoticeRef.current({ ...res, code: res.code || 'PTY_WRITE_FAILED' });
          }
        },
        onError: (err) => {
          if (mountedRef.current) {
            showNoticeRef.current({ code: 'PTY_WRITE_FAILED', message: err?.message || String(err) });
          }
        }
      });
      inputSendersRef.current[paneId] = sender;
    }
    return sender.push(payload);
  }, [api, apiRef, mountedRef, pollersRef]);

  const handleSetMaxPanes = useCallback(async (value) => {
    if (!api?.pty_set_max_panes) return;
    try {
      const res = await api.pty_set_max_panes(value);
      if (!mountedRef.current) return;
      if (res?.success) {
        setCatalog((prev) => ({
          ...prev,
          maxPanes: Number.isInteger(res.max_panes) ? res.max_panes : value,
          hardMaxPanes: Number.isInteger(res.hard_max_panes) ? res.hard_max_panes : prev.hardMaxPanes
        }));
      } else {
        showNotice(res || { code: 'INVALID_ARGUMENT', message: 'No se pudo cambiar el máximo de paneles.' });
      }
    } catch (err) {
      if (mountedRef.current) showNotice({ code: 'INTERNAL_ERROR', message: err?.message || String(err) });
    }
  }, [api, mountedRef, setCatalog, showNotice]);

  const handleSetCliPath = useCallback(async (cliId, path) => {
    if (!api?.pty_set_cli_path) return;
    try {
      const res = await runWithNativeDialogNotice(() => api.pty_set_cli_path(cliId, path));
      if (!mountedRef.current) return;
      if (res?.success) {
        setNotice(null);
        await refreshCatalog();
      } else {
        showNotice(res || { code: 'INVALID_PATH', message: 'No se pudo guardar la ruta.' }, { cliId });
      }
    } catch (err) {
      if (mountedRef.current) showNotice({ code: 'INTERNAL_ERROR', message: err?.message || String(err) }, { cliId });
    }
  }, [api, mountedRef, refreshCatalog, runWithNativeDialogNotice, setNotice, showNotice]);

  // D2: Auto-Relay POR PANEL. Activarlo abre el dialogo nativo en el puente; el estado se persiste alli.
  const handleTogglePaneAutoRelay = useCallback(async (paneId) => {
    const pane = panesRef.current[paneId];
    if (!api?.pty_set_auto_relay || !pane || pane.autoRelayBusy) return;
    const nextEnabled = !pane.autoRelayEnabled;
    patchPane(paneId, { autoRelayBusy: true });
    try {
      const invoke = () => api.pty_set_auto_relay(paneId, nextEnabled);
      const res = await (nextEnabled ? runWithNativeDialogNotice(invoke) : invoke());
      if (!mountedRef.current) return;
      if (res?.success === false) {
        showNotice(res);
        patchPane(paneId, { autoRelayBusy: false });
        return;
      }
      const enabledVal = typeof res?.auto_relay_enabled === 'boolean' ? res.auto_relay_enabled : nextEnabled;
      patchPane(paneId, { autoRelayEnabled: enabledVal, autoRelayBusy: false });
    } catch (err) {
      if (mountedRef.current) {
        patchPane(paneId, { autoRelayBusy: false });
        showNotice({ code: 'INTERNAL_ERROR', message: err?.message || String(err) });
      }
    }
  }, [api, panesRef, mountedRef, patchPane, runWithNativeDialogNotice, showNotice]);

  // F2.4: modo trabajador POR PANEL. Activarlo abre el dialogo nativo en el puente; el estado se persiste alli.
  const handleTogglePaneWorkerMode = useCallback(async (paneId) => {
    const pane = panesRef.current[paneId];
    if (!api?.pty_set_worker_mode || !pane || pane.workerModeBusy) return;
    const nextEnabled = !pane.workerMode;
    patchPane(paneId, { workerModeBusy: true });
    try {
      const invoke = () => api.pty_set_worker_mode(paneId, nextEnabled);
      const res = await (nextEnabled ? runWithNativeDialogNotice(invoke) : invoke());
      if (!mountedRef.current) return;
      if (res?.success === false) {
        showNotice(res);
        patchPane(paneId, { workerModeBusy: false });
        return;
      }
      const enabledVal = typeof res?.worker_mode === 'boolean' ? res.worker_mode : nextEnabled;
      patchPane(paneId, { workerMode: enabledVal, workerModeBusy: false });
    } catch (err) {
      if (mountedRef.current) {
        patchPane(paneId, { workerModeBusy: false });
        showNotice({ code: 'INTERNAL_ERROR', message: err?.message || String(err) });
      }
    }
  }, [api, panesRef, mountedRef, patchPane, runWithNativeDialogNotice, showNotice]);

  const handleRegisterAgyMcp = useCallback(async () => {
    if (!api?.pty_register_agy_mcp) return;
    try {
      const res = await runWithNativeDialogNotice(() => api.pty_register_agy_mcp());
      if (!mountedRef.current) return;
      if (res?.success === false) {
        showNotice(res);
        return;
      }
      setNotice({
        ...describeTerminalError({ code: 'AGY_MCP_REGISTERED', message: res?.message || 'MCP2P registrado en Antigravity.' }),
        tone: 'info',
        title: 'Antigravity'
      });
    } catch (err) {
      if (mountedRef.current) showNotice({ code: 'INTERNAL_ERROR', message: err?.message || String(err) });
    }
  }, [api, mountedRef, runWithNativeDialogNotice, setNotice, showNotice]);

  const handleUnregisterAgyMcp = useCallback(async () => {
    if (!api?.pty_unregister_agy_mcp || unregisterAgyBusy) return;
    setUnregisterAgyBusy(true);
    try {
      const res = await api.pty_unregister_agy_mcp();
      if (!mountedRef.current) return;
      if (res?.success === false) {
        showNotice(res);
        return;
      }
      setNotice({
        ...describeTerminalError({
          code: 'AGY_MCP_UNREGISTERED',
          message: res?.message || 'Se quitó el registro de MCP2P en Antigravity.'
        }),
        tone: 'info',
        title: 'Antigravity'
      });
      await refreshMatrixStatus();
    } catch (err) {
      if (mountedRef.current) showNotice({ code: 'INTERNAL_ERROR', message: err?.message || String(err) });
    } finally {
      if (mountedRef.current) setUnregisterAgyBusy(false);
    }
  }, [api, mountedRef, unregisterAgyBusy, setNotice, showNotice, refreshMatrixStatus]);

  const trippedCases = useMemo(() => {
    const raw = matrixStatus.relayStatus?.tripped_cases;
    if (!Array.isArray(raw)) return [];
    return raw.map((item) => parseTrippedCase(item, project?.project_uuid || '')).filter((c) => c.label);
  }, [matrixStatus.relayStatus?.tripped_cases, project?.project_uuid]);

  const handleResetLoopBreaker = useCallback(async () => {
    if (!api?.reset_loop_breaker || resettingBreaker || trippedCases.length === 0) return;
    setResettingBreaker(true);
    try {
      const first = trippedCases[0];
      const res = await runWithNativeDialogNotice(() =>
        api.reset_loop_breaker(first.projectUuid, first.cardId, first.sessionId)
      );
      if (!mountedRef.current) return;
      if (res?.success === false) {
        showNotice(res);
      } else {
        await refreshMatrixStatus();
      }
    } catch (err) {
      if (mountedRef.current) {
        showNotice({ code: 'INTERNAL_ERROR', message: err?.message || String(err) });
      }
    } finally {
      if (mountedRef.current) setResettingBreaker(false);
    }
  }, [api, mountedRef, resettingBreaker, trippedCases, runWithNativeDialogNotice, showNotice, refreshMatrixStatus]);

  return {
    launchingCliId,
    unregisterAgyBusy,
    resettingBreaker,
    trippedCases,
    markPaneOpened,
    handleLaunchCli,
    handlePausePane,
    handleClosePane,
    handleResizePane,
    sendPaneInput,
    handleSetMaxPanes,
    handleSetCliPath,
    handleTogglePaneAutoRelay,
    handleTogglePaneWorkerMode,
    handleRegisterAgyMcp,
    handleUnregisterAgyMcp,
    handleResetLoopBreaker
  };
}
