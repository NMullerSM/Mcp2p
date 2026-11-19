import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPanePoller } from './panePoller';
import {
  acquireTerminal,
  getTerminalEntry,
  writeTerminalNotice
} from './terminalRegistry';
import {
  DEFAULT_MAX_PANES,
  DEFAULT_PTY_SIZE,
  HARD_MAX_PANES,
  MATRIX_PANE_IDS,
  computeGridLayout,
  computePollDelay,
  describeTerminalError,
  paneNumber,
  recallPaneSession
} from './terminalModel';

export function createInitialPaneState(paneId) {
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
    exitCode: null,
    mcpInjection: null,
    activityState: null,
    activitySource: '',
    // Ola 2: proyecto al que pertenece el panel ('' = sin proyecto, visible siempre) y su Auto-Relay (D2).
    projectUuid: '',
    autoRelayEnabled: false,
    autoRelayBusy: false,
    // F2.4: modo trabajador por panel (persistido en el backend junto al Auto-Relay).
    workerMode: false,
    workerModeBusy: false
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

/**
 * Estado del enjambre de terminales: paneles, catálogo de CLIs, estado de matriz,
 * sondeo adaptativo de salida, visibilidad por proyecto y foco.
 */
export function useMatrixState({ api, project, searchQuery = '', active = true }) {
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
    loading: false,
    autoRelayEnabled: false,
    autoRelayBusy: false,
    relayStatus: { auto_relay_enabled: false, tripped_cases: [] }
  });
  const [catalog, setCatalog] = useState(INITIAL_CATALOG);
  const [notice, setNotice] = useState(null);
  const [pendingNativeConfirmations, setPendingNativeConfirmations] = useState(0);

  const mountedRef = useRef(true);
  const panesRef = useRef(panes);
  panesRef.current = panes;
  const catalogRef = useRef(catalog);
  catalogRef.current = catalog;
  const activeRef = useRef(active);
  activeRef.current = active;
  const apiRef = useRef(api);
  apiRef.current = api;
  const pollersRef = useRef({});
  const terminalRefs = useRef({});

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
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

  const runWithNativeDialogNotice = useCallback(async (fn) => {
    setPendingNativeConfirmations((prev) => prev + 1);
    try {
      return await fn();
    } finally {
      if (mountedRef.current) {
        setPendingNativeConfirmations((prev) => Math.max(0, prev - 1));
      }
    }
  }, []);

  // ---- Estado del enjambre y catálogo -------------------------------------------------

  const refreshMatrixStatus = useCallback(async () => {
    if (!api?.pty_get_matrix_status) return;
    setMatrixStatus((prev) => ({ ...prev, loading: true }));
    try {
      const res = await api.pty_get_matrix_status();
      if (!mountedRef.current || !res) return;

      const relayStatus = res.relay_status || {
        auto_relay_enabled: Boolean(res.auto_relay_enabled),
        tripped_cases: []
      };
      const autoRelayEnabled = Boolean(
        res.auto_relay_enabled !== undefined ? res.auto_relay_enabled : relayStatus.auto_relay_enabled
      );
      setMatrixStatus((prev) => ({
        ...prev,
        degraded: Boolean(res.degraded),
        platformUnsupported: Boolean(res.platform_unsupported) || res.code === 'TERMINAL_PLATFORM_UNSUPPORTED',
        hubUrl: res.hub_url || null,
        hubRunning: Boolean(res.hub_running),
        manualCommand: res.manual_command || null,
        mcpConfig: res.mcp_config || null,
        loading: false,
        autoRelayEnabled,
        relayStatus
      }));

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
              registryStatus: r?.status || base.registryStatus,
              mcpInjection: s.mcp_injection ?? r?.mcp_injection ?? base.mcpInjection ?? null,
              activityState: s.activity_state ?? r?.activity_state ?? base.activityState ?? null,
              activitySource: s.activity_source ?? r?.activity_source ?? base.activitySource ?? '',
              projectUuid: r?.project_uuid ?? base.projectUuid ?? '',
              autoRelayEnabled: Boolean(r?.auto_relay_enabled ?? base.autoRelayEnabled),
              workerMode: Boolean(r?.worker_mode ?? base.workerMode)
            };
          } else if (cur.open && cur.alive) {
            next[paneId] = { ...cur, alive: false, paused: false, rssMb: 0 };
          } else if (cur.open && r) {
            next[paneId] = {
              ...cur,
              nodeUuid: r.node_uuid || cur.nodeUuid,
              pid: r.pid ?? cur.pid,
              registryStatus: r.status || cur.registryStatus,
              mcpInjection: r.mcp_injection ?? cur.mcpInjection ?? null,
              activityState: r.activity_state ?? cur.activityState ?? null,
              activitySource: r.activity_source ?? cur.activitySource ?? '',
              projectUuid: r.project_uuid ?? cur.projectUuid ?? '',
              autoRelayEnabled: Boolean(r.auto_relay_enabled ?? cur.autoRelayEnabled),
              workerMode: Boolean(r.worker_mode ?? cur.workerMode)
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
      if (Array.isArray(res.removed_profiles) && res.removed_profiles.length > 0) {
        const removedMsg = res.removed_profiles.map((r) => r?.message).filter(Boolean).join(' ')
          || 'Gemini CLI ya no está en el catálogo: usa Antigravity.';
        setNotice((prev) => prev || {
          code: 'CLI_PROFILE_REMOVED',
          tone: 'info',
          title: 'Catálogo de CLIs actualizado',
          message: removedMsg,
          actions: []
        });
      }
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
    const activityState = res.activity_state !== undefined ? res.activity_state : before.activityState;
    const activitySource = res.activity_source !== undefined ? res.activity_source : before.activitySource;
    const mcpInjection = res.mcp_injection !== undefined ? res.mcp_injection : before.mcpInjection;
    // Solo se re-renderiza si cambia algo visible (un sondeo cada 30 ms no debe repintar el panel).
    const changed = before.alive !== stillAlive
      || before.paused !== paused
      || before.exitCode !== exitCode
      || before.activityState !== activityState
      || before.activitySource !== activitySource
      || before.mcpInjection !== mcpInjection
      || Math.abs((before.rssMb || 0) - rssMb) >= 0.5
      || (rssMb === 0 && before.rssMb !== 0);
    if (changed) {
      setPanes((prev) => {
        const cur = prev[paneId];
        if (!cur || !cur.open) return prev;
        return {
          ...prev,
          [paneId]: {
            ...cur,
            alive: stillAlive,
            paused,
            rssMb,
            exitCode,
            activityState,
            activitySource,
            mcpInjection
          }
        };
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
  // D7: al cambiar de proyecto, los paneles de otros proyectos siguen vivos pero ocultos (no se desmontan ni
  // se cierran). Un panel sin proyecto (instalador, o abierto sin proyecto activo) se ve siempre.
  const activeProjectUuid = project?.project_uuid || '';
  const projectPaneIds = openPaneIds.filter((id) => !panes[id].projectUuid || panes[id].projectUuid === activeProjectUuid);
  const hiddenPaneCount = openPaneIds.length - projectPaneIds.length;
  const projectPaneIdsKey = projectPaneIds.join(',');

  useEffect(() => {
    // Si el panel enfocado se cierra o pasa a otro proyecto, el foco pasa al primero visible.
    if (!projectPaneIds.length || projectPaneIds.includes(focusedPaneId)) return;
    setFocusedPaneId(projectPaneIds[0]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectPaneIdsKey, focusedPaneId]);

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
    ? projectPaneIds.filter((paneId) => {
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
    : projectPaneIds;
  const visiblePaneSet = new Set(visibleOpenIds);

  const layout = computeGridLayout(visibleOpenIds.length);
  const emptySlots = Math.max(0, layout.cells - visibleOpenIds.length);
  const noOpenTerminals = projectPaneIds.length === 0;
  const searchHidesAll = normalizedSearch && projectPaneIds.length > 0 && visibleOpenIds.length === 0;

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

  const activePanes = projectPaneIds.map((id) => panes[id]);
  const relayTargets = activePanes
    .filter((p) => p.alive)
    .map((p) => ({ paneId: p.paneId, label: `${paneLabels[p.paneId]} · ${catalogById[p.cliProfile]?.display_name || p.cliProfile}` }));

  return {
    panes,
    setPanes,
    panesRef,
    focusedPaneId,
    focusRequest,
    matrixStatus,
    setMatrixStatus,
    catalog,
    setCatalog,
    catalogRef,
    catalogById,
    maxPanes,
    notice,
    setNotice,
    showNotice,
    pendingNativeConfirmations,
    runWithNativeDialogNotice,
    mountedRef,
    apiRef,
    pollersRef,
    refreshMatrixStatus,
    refreshCatalog,
    refreshAll,
    openPaneIds,
    projectPaneIds,
    hiddenPaneCount,
    requestTerminalFocus,
    handleClickPane,
    registerTerminalRef,
    paneLabels,
    visiblePaneSet,
    layout,
    emptySlots,
    noOpenTerminals,
    searchHidesAll,
    statusNotice,
    activePanes,
    relayTargets
  };
}
