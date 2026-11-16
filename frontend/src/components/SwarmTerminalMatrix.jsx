import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { ExternalLink, X } from 'lucide-react';
import { ViewSliderContent, useViewSlider } from './ViewSlider';
import { EmptyTerminalSlot, TerminalPane } from './terminals/TerminalPane';
import { createPanePoller } from './terminals/panePoller';
import { createInputSender } from './terminals/inputSender';
import {
  acquireTerminal,
  disposeTerminal,
  getTerminalEntry,
  writeTerminalNotice
} from './terminals/terminalRegistry';
import { TerminalSliderControls } from './terminals/TerminalSliderControls';
import {
  DEFAULT_MAX_PANES,
  DEFAULT_PTY_SIZE,
  HARD_MAX_PANES,
  MATRIX_PANE_IDS,
  computeGridLayout,
  computePollDelay,
  describeTerminalError,
  forgetPaneSession,
  paneNumber,
  pickFreePaneId,
  recallPaneSession,
  rememberPaneSession
} from './terminals/terminalModel';

// Compatibilidad: estos nombres se exportaban desde este modulo antes de partirlo.
export { MATRIX_PANE_IDS, ROLE_OPTIONS, stripAnsiSequences } from './terminals/terminalModel';

function createInitialPaneState(paneId) {
  return {
    paneId,
    open: false,
    kind: 'cli',
    cliProfile: 'claude',
    role: 'implementer',
    cwd: null,
    cols: DEFAULT_PTY_SIZE.cols,
    rows: DEFAULT_PTY_SIZE.rows,
    alive: false,
    paused: false,
    pid: null,
    rssMb: 0,
    nodeUuid: '',
    sessionId: '',
    registryStatus: '',
    exitCode: null
  };
}

function createInitialPanesMap() {
  const map = {};
  MATRIX_PANE_IDS.forEach((paneId) => {
    map[paneId] = createInitialPaneState(paneId);
  });
  return map;
}

const INITIAL_CATALOG = {
  loaded: false,
  loading: false,
  error: null,
  clis: [],
  maxPanes: DEFAULT_MAX_PANES,
  hardMaxPanes: HARD_MAX_PANES,
  platformSupported: true,
  platform: null,
  warnings: [],
  version: null
};

function ExternalUrlConfirmDialog({ url, onCancel, onConfirm }) {
  if (!url) return null;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Confirmar apertura de URL externa"
      data-testid="external-url-confirm-modal"
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.58)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '16px',
        zIndex: 120
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: '460px',
          backgroundColor: 'var(--bg-surface)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-lg)',
          padding: '16px',
          display: 'flex',
          flexDirection: 'column',
          gap: '12px'
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '7px', fontSize: '13.5px', fontWeight: 700, color: 'var(--text-primary)' }}>
            <ExternalLink size={15} color="var(--accent-warning)" />
            <span>Confirmar enlace externo desde terminal</span>
          </div>
          <button
            type="button"
            aria-label="Cerrar diálogo"
            onClick={onCancel}
            style={{ border: 'none', background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer', display: 'inline-flex' }}
          >
            <X size={15} />
          </button>
        </div>

        <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
          Esta URL apareció en la salida de un terminal. Confirma explícitamente el dominio antes de abrirla para evitar aperturas involuntarias o phishing:
        </p>

        <pre
          style={{
            margin: 0,
            padding: '8px 10px',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-app)',
            border: '1px solid var(--border-subtle)',
            fontFamily: 'var(--font-mono)',
            fontSize: '11.5px',
            color: 'var(--accent-primary)',
            wordBreak: 'break-all',
            whiteSpace: 'pre-wrap',
            userSelect: 'text'
          }}
        >
          {url}
        </pre>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
          <button
            type="button"
            data-testid="external-url-cancel-btn"
            onClick={onCancel}
            style={{
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            Cancelar
          </button>
          <button
            type="button"
            data-testid="external-url-confirm-btn"
            onClick={onConfirm}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 700,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: 'var(--accent-primary)',
              color: '#ffffff',
              cursor: 'pointer'
            }}
          >
            <ExternalLink size={13} />
            <span>Abrir enlace</span>
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * `active` = la vista de terminales esta a la vista. ProjectWorkspace la mantiene montada (oculta)
 * al cambiar de pestana para que el sondeo y las instancias de xterm sigan vivas; mientras esta
 * oculta no registra su contenido en el slider y sondea despacio.
 */
export function SwarmTerminalMatrix({ api, project, searchQuery = '', active = true }) {
  const { close: closeSlider, open: openSlider, isOpen: sliderOpen } = useViewSlider();
  const [panes, setPanes] = useState(() => createInitialPanesMap());
  const [focusedPaneId, setFocusedPaneId] = useState('pane_1');
  const [focusRequest, setFocusRequest] = useState({ paneId: null, nonce: 0 });
  const [matrixStatus, setMatrixStatus] = useState({
    degraded: false,
    platformUnsupported: false,
    hubUrl: null,
    hubRunning: false,
    manualCommand: null,
    mcpConfig: null,
    loading: false
  });
  const [catalog, setCatalog] = useState(INITIAL_CATALOG);
  const [notice, setNotice] = useState(null);
  const [cwdInput, setCwdInput] = useState('');
  const [roleInput, setRoleInput] = useState('implementer');
  const [launchingCliId, setLaunchingCliId] = useState(null);
  const [copiedKey, setCopiedKey] = useState(null);
  const [pendingExternalUrl, setPendingExternalUrl] = useState(null);
  // Plan de instalacion pendiente de confirmar. El token de un solo uso vive SOLO en
  // installTokenRef (memoria de este componente): no se pinta, no se guarda, no se registra.
  const [installPlan, setInstallPlan] = useState(null);
  const [installBusyCliId, setInstallBusyCliId] = useState(null);
  const [pathEditRequest, setPathEditRequest] = useState({ cliId: null, nonce: 0 });

  const mountedRef = useRef(true);
  const panesRef = useRef(panes);
  panesRef.current = panes;
  const catalogRef = useRef(catalog);
  catalogRef.current = catalog;
  const activeRef = useRef(active);
  activeRef.current = active;
  const pollersRef = useRef({});
  // Un emisor de entrada por panel: una sola llamada pty_write_input en vuelo y lo tecleado
  // mientras tanto sale agrupado en la siguiente, en orden (EGM-80: cada llamada del puente
  // corre en su propio hilo de Python y dos solapadas pueden llegar al PTY al reves).
  const inputSendersRef = useRef({});
  const launchingRef = useRef(false);
  const installTokenRef = useRef(null);
  const installingRef = useRef(false);
  const terminalRefs = useRef({});

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      installTokenRef.current = null;
    };
  }, []);

  const catalogById = useMemo(() => {
    const map = {};
    catalog.clis.forEach((cli) => {
      map[cli.id] = cli;
    });
    return map;
  }, [catalog.clis]);

  const maxPanes = catalog.maxPanes || DEFAULT_MAX_PANES;

  const showNotice = useCallback((res, context = {}) => {
    setNotice(describeTerminalError(res, { maxPanes: catalogRef.current.maxPanes, ...context }));
  }, []);

  const handleCopy = useCallback(async (key, text) => {
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      }
      if (mountedRef.current) {
        setCopiedKey(key);
        setTimeout(() => {
          if (mountedRef.current) {
            setCopiedKey((prev) => (prev === key ? null : prev));
          }
        }, 1800);
      }
    } catch {
      // Ignorar si clipboard no está disponible en entorno restringido
    }
  }, []);

  // ---- Estado del enjambre y catálogo -------------------------------------------------

  const refreshMatrixStatus = useCallback(async () => {
    if (!api?.pty_get_matrix_status) return;
    setMatrixStatus((prev) => ({ ...prev, loading: true }));
    try {
      const res = await api.pty_get_matrix_status();
      if (!mountedRef.current || !res) return;

      setMatrixStatus({
        degraded: Boolean(res.degraded),
        platformUnsupported: Boolean(res.platform_unsupported) || res.code === 'TERMINAL_PLATFORM_UNSUPPORTED',
        hubUrl: res.hub_url || null,
        hubRunning: Boolean(res.hub_running),
        manualCommand: res.manual_command || null,
        mcpConfig: res.mcp_config || null,
        loading: false
      });

      const sessionMap = {};
      (res.sessions || []).forEach((s) => {
        if (s?.pane_id) sessionMap[s.pane_id] = s;
      });
      const registryMap = {};
      (res.panes || []).forEach((p) => {
        if (p?.pane_id) registryMap[p.pane_id] = p;
      });

      setPanes((prev) => {
        const next = { ...prev };
        MATRIX_PANE_IDS.forEach((paneId) => {
          const s = sessionMap[paneId];
          const r = registryMap[paneId];
          const cur = prev[paneId];
          if (s && s.alive) {
            // Una sesión viva siempre se muestra (p. ej. al volver a esta pestaña).
            const remembered = cur.open ? null : recallPaneSession(paneId, s.session_id);
            const base = cur.open ? cur : { ...createInitialPaneState(paneId), open: true };
            next[paneId] = {
              ...base,
              kind: remembered?.kind || base.kind,
              cwd: s.cwd || base.cwd || remembered?.cwd || null,
              cliProfile: s.cli_profile || r?.cli_profile || base.cliProfile,
              role: s.role || r?.role || base.role,
              cols: s.cols || base.cols,
              rows: s.rows || base.rows,
              alive: true,
              paused: Boolean(s.paused),
              pid: s.pid ?? r?.pid ?? base.pid,
              rssMb: typeof s.rss_mb === 'number' ? s.rss_mb : base.rssMb,
              nodeUuid: s.node_uuid || r?.node_uuid || base.nodeUuid,
              sessionId: s.session_id || base.sessionId,
              registryStatus: r?.status || base.registryStatus
            };
          } else if (cur.open && cur.alive) {
            next[paneId] = { ...cur, alive: false, paused: false, rssMb: 0 };
          } else if (cur.open && r) {
            next[paneId] = {
              ...cur,
              nodeUuid: r.node_uuid || cur.nodeUuid,
              pid: r.pid ?? cur.pid,
              registryStatus: r.status || cur.registryStatus
            };
          }
        });
        return next;
      });
    } catch {
      if (mountedRef.current) {
        setMatrixStatus((prev) => ({ ...prev, loading: false }));
      }
    }
  }, [api]);

  const refreshCatalog = useCallback(async () => {
    if (!api?.pty_list_cli_catalog) {
      setCatalog((prev) => ({ ...prev, loaded: true, loading: false }));
      return;
    }
    setCatalog((prev) => ({ ...prev, loading: true }));
    try {
      const res = await api.pty_list_cli_catalog();
      if (!mountedRef.current) return;
      if (!res || res.success === false) {
        setCatalog((prev) => ({
          ...prev,
          loaded: true,
          loading: false,
          error: res?.message || 'No se pudo leer el catálogo de CLIs.'
        }));
        return;
      }
      setCatalog({
        loaded: true,
        loading: false,
        error: null,
        clis: Array.isArray(res.clis) ? res.clis : [],
        maxPanes: Number.isInteger(res.max_panes) ? res.max_panes : DEFAULT_MAX_PANES,
        hardMaxPanes: Number.isInteger(res.hard_max_panes) ? res.hard_max_panes : HARD_MAX_PANES,
        platformSupported: res.terminal_platform_supported !== false,
        platform: res.platform || null,
        warnings: Array.isArray(res.warnings) ? res.warnings : [],
        version: res.catalog_version || null
      });
    } catch (err) {
      if (mountedRef.current) {
        setCatalog((prev) => ({ ...prev, loaded: true, loading: false, error: err?.message || String(err) }));
      }
    }
  }, [api]);

  const refreshAll = useCallback(async () => {
    await Promise.all([refreshMatrixStatus(), refreshCatalog()]);
  }, [refreshMatrixStatus, refreshCatalog]);

  useEffect(() => {
    void refreshAll();
  }, [refreshAll, project?.project_uuid]);

  // ---- Sondeo de la salida ------------------------------------------------------------
  // La salida NO pasa por el estado de React: va directa a la instancia de xterm del panel
  // (terminalRegistry). Un bucle por panel vivo, que espera cada respuesta antes de pedir la
  // siguiente y cuyo ritmo se adapta a la actividad (computePollDelay).

  const apiRef = useRef(api);
  apiRef.current = api;

  const applyPollResult = useCallback((paneId, res, since) => {
    if (!mountedRef.current) return false;
    const before = panesRef.current[paneId];
    if (!before || !before.open) return false;

    const entry = acquireTerminal(paneId, { sessionId: before.sessionId, cols: before.cols, rows: before.rows });
    // Una respuesta que no continua desde el cursor actual es un duplicado (p. ej. de un bucle
    // que ya se habia sustituido): el texto ya se escribio, no se repite.
    const duplicate = since !== entry.nextSeq;
    if (!duplicate) {
      if (typeof res.next_seq === 'number') entry.nextSeq = res.next_seq;
      if (res.truncated) writeTerminalNotice(entry, 'salida anterior truncada del buffer');
      if (res.text) {
        entry.term.write(res.text);
        entry.lastActivityAt = Date.now();
      }
    }

    const stillAlive = Boolean(res.alive);
    const exitCode = res.exit_code !== undefined ? res.exit_code : before.exitCode;
    if (!stillAlive && !entry.exitAnnounced && exitCode !== null && exitCode !== undefined) {
      entry.exitAnnounced = true;
      writeTerminalNotice(entry, `proceso terminado, código ${exitCode}`);
    }

    const paused = Boolean(res.paused);
    const rssMb = stillAlive ? (typeof res.rss_mb === 'number' ? res.rss_mb : before.rssMb) : 0;
    // Solo se re-renderiza si cambia algo visible (un sondeo cada 30 ms no debe repintar el panel).
    const changed = before.alive !== stillAlive
      || before.paused !== paused
      || before.exitCode !== exitCode
      || Math.abs((before.rssMb || 0) - rssMb) >= 0.5
      || (rssMb === 0 && before.rssMb !== 0);
    if (changed) {
      setPanes((prev) => {
        const cur = prev[paneId];
        if (!cur || !cur.open) return prev;
        return { ...prev, [paneId]: { ...cur, alive: stillAlive, paused, rssMb, exitCode } };
      });
    }

    if (res.degraded && res.code) {
      setNotice((prev) => (prev?.code === res.code ? prev : describeTerminalError(res)));
    }
    if (before.alive && !stillAlive && before.kind === 'install') {
      void refreshCatalog();
    }
    return stillAlive;
  }, [refreshCatalog]);
  const applyPollResultRef = useRef(applyPollResult);
  applyPollResultRef.current = applyPollResult;

  const alivePaneIdsKey = MATRIX_PANE_IDS.filter((id) => panes[id].open && panes[id].alive).join(',');

  useEffect(() => {
    const wanted = new Set(alivePaneIdsKey ? alivePaneIdsKey.split(',') : []);
    // Fuera los bucles de paneles que ya no estan vivos (o que se detuvieron solos).
    Object.keys(pollersRef.current).forEach((paneId) => {
      const poller = pollersRef.current[paneId];
      if (!wanted.has(paneId) || !poller.isRunning()) {
        poller.stop();
        delete pollersRef.current[paneId];
      }
    });
    if (!api?.pty_poll_output) return;
    wanted.forEach((paneId) => {
      if (pollersRef.current[paneId]) return; // un solo bucle por panel
      const poller = createPanePoller({
        poll: async () => {
          const pane = panesRef.current[paneId];
          const entry = acquireTerminal(paneId, { sessionId: pane?.sessionId, cols: pane?.cols, rows: pane?.rows });
          const since = entry.nextSeq;
          const res = await apiRef.current.pty_poll_output(paneId, since);
          return res ? { res, since } : null;
        },
        onResult: ({ res, since }) => applyPollResultRef.current(paneId, res, since),
        getDelay: () => computePollDelay({
          now: Date.now(),
          lastActivityAt: getTerminalEntry(paneId)?.lastActivityAt || 0,
          hidden: !activeRef.current
        })
      });
      pollersRef.current[paneId] = poller;
      poller.start();
    });
  }, [alivePaneIdsKey, api]);

  useEffect(() => () => {
    Object.values(pollersRef.current).forEach((poller) => poller.stop());
    pollersRef.current = {};
  }, []);

  // ---- Foco ---------------------------------------------------------------------------

  const openPaneIds = MATRIX_PANE_IDS.filter((id) => panes[id].open);
  const openPaneIdsKey = openPaneIds.join(',');

  useEffect(() => {
    // Si el panel enfocado se cierra, el foco pasa al primero abierto.
    if (!openPaneIds.length || openPaneIds.includes(focusedPaneId)) return;
    setFocusedPaneId(openPaneIds[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openPaneIdsKey, focusedPaneId]);

  const requestTerminalFocus = useCallback((paneId) => {
    setFocusedPaneId(paneId);
    setFocusRequest((prev) => ({ paneId, nonce: prev.nonce + 1 }));
  }, []);

  const handleClickPane = useCallback((paneId) => {
    setFocusedPaneId(paneId);
    terminalRefs.current[paneId]?.focus();
  }, []);

  const registerTerminalRef = useCallback((paneId, el) => {
    terminalRefs.current[paneId] = el;
  }, []);

  // Al volver a la vista (sigue montada mientras esta oculta): sondeo rapido, estado fresco del
  // backend y el teclado de nuevo en la terminal enfocada.
  const focusedPaneIdRef = useRef(focusedPaneId);
  focusedPaneIdRef.current = focusedPaneId;
  const wasActiveRef = useRef(active);
  useEffect(() => {
    if (active && !wasActiveRef.current) {
      const now = Date.now();
      MATRIX_PANE_IDS.forEach((id) => {
        const entry = getTerminalEntry(id);
        if (entry) entry.lastActivityAt = now;
      });
      void refreshAll();
      terminalRefs.current[focusedPaneIdRef.current]?.focus();
    }
    wasActiveRef.current = active;
  }, [active, refreshAll]);

  // Seleccionar una terminal en el slider la enfoca y deja el teclado listo para escribir.
  const handleFocusFromSlider = useCallback((paneId) => {
    requestTerminalFocus(paneId);
    closeSlider();
  }, [requestTerminalFocus, closeSlider]);

  // ---- Acciones sobre terminales ------------------------------------------------------

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
  }, []);

  const handleLaunchCli = useCallback(async (cliId) => {
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
      const res = await api.pty_spawn_pane(
        paneId,
        cliId,
        roleInput,
        null,
        DEFAULT_PTY_SIZE.cols,
        DEFAULT_PTY_SIZE.rows,
        true,
        cwd
      );
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
        registryStatus: paneMeta.status || ''
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
  }, [api, cwdInput, roleInput, showNotice, markPaneOpened, requestTerminalFocus, closeSlider]);

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
  }, [api, showNotice]);

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
  }, [api]);

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
  }, [api]);

  const showNoticeRef = useRef(showNotice);
  showNoticeRef.current = showNotice;

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
  }, [api]);

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
  }, [api, showNotice]);

  // ---- Instalar un CLI que falta (dos pasos, siempre con confirmacion) ------------------

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
  }, [api, showNotice, discardInstallPlan]);

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
      const res = await api.pty_install_cli(plan.cliId, token);
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
  }, [api, installPlan, discardInstallPlan, showNotice, markPaneOpened, requestTerminalFocus, closeSlider, refreshMatrixStatus]);

  // La confirmacion vence (el backend la invalida a los 120 s); se retira tambien de la pantalla.
  useEffect(() => {
    if (!installPlan) return undefined;
    const timer = setTimeout(() => {
      installTokenRef.current = null;
      setInstallPlan(null);
      setNotice(describeTerminalError({ code: 'INSTALL_TOKEN_EXPIRED' }));
    }, installPlan.expiresInS * 1000);
    return () => clearTimeout(timer);
  }, [installPlan]);

  // ---- Ruta del binario -----------------------------------------------------------------

  const handleSetCliPath = useCallback(async (cliId, path) => {
    if (!api?.pty_set_cli_path) return;
    try {
      const res = await api.pty_set_cli_path(cliId, path);
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
  }, [api, refreshCatalog, showNotice]);

  const handleNoticeAction = useCallback((action) => {
    if (action.kind === 'install') {
      void handlePrepareInstall(action.cliId);
    } else if (action.kind === 'configure_path') {
      setPathEditRequest((prev) => ({ cliId: action.cliId, nonce: prev.nonce + 1 }));
    }
  }, [handlePrepareInstall]);


  // ---- Derivados para pintar ----------------------------------------------------------

  const paneLabels = useMemo(() => {
    const labels = {};
    MATRIX_PANE_IDS.forEach((id) => {
      labels[id] = `Terminal ${paneNumber(id)}`;
    });
    return labels;
  }, []);

  const normalizedSearch = (searchQuery || '').trim().toLowerCase();
  const visibleOpenIds = normalizedSearch
    ? openPaneIds.filter((paneId) => {
        const p = panes[paneId];
        const haystack = [
          p.paneId,
          paneLabels[paneId],
          p.cliProfile,
          catalogById[p.cliProfile]?.display_name,
          p.role,
          p.cwd,
          p.nodeUuid
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();
        return haystack.includes(normalizedSearch);
      })
    : openPaneIds;

  const layout = computeGridLayout(visibleOpenIds.length);
  const emptySlots = Math.max(0, layout.cells - visibleOpenIds.length);
  const noOpenTerminals = openPaneIds.length === 0;
  const searchHidesAll = normalizedSearch && openPaneIds.length > 0 && visibleOpenIds.length === 0;

  const statusNotice = useMemo(() => {
    const extra = { manual_command: matrixStatus.manualCommand, mcp_config: matrixStatus.mcpConfig };
    if (matrixStatus.platformUnsupported || catalog.platformSupported === false) {
      return describeTerminalError({ code: 'TERMINAL_PLATFORM_UNSUPPORTED', ...extra });
    }
    if (matrixStatus.degraded) {
      return describeTerminalError({ code: 'TERMINAL_ADAPTER_DEGRADED', ...extra });
    }
    return null;
  }, [matrixStatus, catalog.platformSupported]);

  const activePanes = openPaneIds.map((id) => panes[id]);

  return (
    <div
      data-testid="swarm-terminal-matrix"
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        width: '100%',
        minHeight: 0,
        overflow: 'hidden'
      }}
    >
      {/* Todo el control vive en el slider lateral derecho (seccion 4 del plan). */}
      {active && (
      <ViewSliderContent
        title="Terminales"
        subtitle="Controles del enjambre"
        data-testid="terminals-slider"
      >
        <TerminalSliderControls
          status={matrixStatus}
          statusNotice={statusNotice}
          catalog={catalog}
          maxPanes={maxPanes}
          hardMaxPanes={catalog.hardMaxPanes}
          activePanes={activePanes}
          focusedPaneId={focusedPaneId}
          paneLabels={paneLabels}
          catalogById={catalogById}
          notice={notice}
          onDismissNotice={() => setNotice(null)}
          onNoticeAction={handleNoticeAction}
          cwdValue={cwdInput}
          onCwdChange={setCwdInput}
          roleValue={roleInput}
          onRoleChange={setRoleInput}
          launchingCliId={launchingCliId}
          copiedKey={copiedKey}
          onCopy={handleCopy}
          onRefresh={refreshAll}
          onLaunch={handleLaunchCli}
          onFocusPane={handleFocusFromSlider}
          onPausePane={handlePausePane}
          onClosePane={handleClosePane}
          onSetMaxPanes={handleSetMaxPanes}
          installPlan={installPlan}
          installBusyCliId={installBusyCliId}
          onPrepareInstall={handlePrepareInstall}
          onConfirmInstall={handleConfirmInstall}
          onCancelInstall={discardInstallPlan}
          onSetCliPath={handleSetCliPath}
          pathEditRequest={pathEditRequest}
        />
      </ViewSliderContent>
      )}

      <div
        data-testid="matrix-grid"
        data-layout={layout.id}
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: layout.columns,
          gridTemplateRows: layout.rowTemplate,
          gap: '10px'
        }}
      >
        {searchHidesAll ? (
          <div
            style={{
              gridColumn: '1 / -1',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '28px',
              borderRadius: 'var(--radius-lg)',
              border: '1px dashed var(--border-subtle)',
              color: 'var(--text-muted)',
              fontSize: '12.5px'
            }}
          >
            Ninguna terminal coincide con «{searchQuery}».
          </div>
        ) : (
          <>
            {visibleOpenIds.map((paneId) => (
              <TerminalPane
                key={paneId}
                pane={panes[paneId]}
                label={paneLabels[paneId]}
                cli={catalogById[panes[paneId].cliProfile]}
                isFocused={focusedPaneId === paneId}
                focusToken={focusRequest.paneId === paneId ? focusRequest.nonce : 0}
                onFocus={handleClickPane}
                onInput={sendPaneInput}
                onResize={handleResizePane}
                onRequestOpenUrl={setPendingExternalUrl}
                registerTerminalRef={registerTerminalRef}
              />
            ))}
            {Array.from({ length: emptySlots }, (_, i) => (
              <EmptyTerminalSlot key={`empty-${i}`} index={i} showHint={noOpenTerminals && i === 0} />
            ))}
          </>
        )}
      </div>

      {/* Aviso discreto cuando el slider esta cerrado; el detalle completo vive en el slider. */}
      {notice && !sliderOpen && (
        <div
          role="status"
          data-testid="matrix-notice"
          data-code={notice.code}
          style={{
            position: 'absolute',
            left: '50%',
            bottom: '12px',
            transform: 'translateX(-50%)',
            maxWidth: 'min(560px, 92%)',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            padding: '6px 10px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            boxShadow: 'var(--shadow-md)',
            fontSize: '11.5px',
            color: 'var(--text-primary)'
          }}
        >
          <span>{notice.title}</span>
          <button
            type="button"
            data-testid="matrix-notice-open"
            onClick={() => openSlider()}
            style={{ border: 'none', background: 'transparent', color: 'var(--accent-primary)', fontWeight: 700, cursor: 'pointer', fontSize: '11.5px', padding: 0 }}
          >
            Ver detalle
          </button>
          <button
            type="button"
            aria-label="Descartar aviso"
            data-testid="matrix-notice-dismiss"
            onClick={() => setNotice(null)}
            style={{ border: 'none', background: 'transparent', color: 'var(--text-muted)', cursor: 'pointer', display: 'inline-flex', padding: 0 }}
          >
            <X size={13} />
          </button>
        </div>
      )}

      <ExternalUrlConfirmDialog
        url={pendingExternalUrl}
        onCancel={() => setPendingExternalUrl(null)}
        onConfirm={() => {
          const target = pendingExternalUrl;
          setPendingExternalUrl(null);
          if (typeof window !== 'undefined' && typeof window.open === 'function') {
            window.open(target, '_blank', 'noopener,noreferrer');
          }
        }}
      />
    </div>
  );
}
