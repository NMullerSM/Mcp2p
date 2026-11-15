import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Terminal,
  Play,
  Pause,
  Square,
  RefreshCw,
  Send,
  Copy,
  Check,
  AlertTriangle,
  ExternalLink,
  X,
  Focus
} from 'lucide-react';

export const MATRIX_PANE_IDS = ['pane_1', 'pane_2', 'pane_3', 'pane_4'];

export const CLI_PROFILE_OPTIONS = [
  { value: 'claude', label: 'Claude Code (claude)' },
  { value: 'gemini', label: 'Gemini CLI (gemini)' },
  { value: 'codex', label: 'Codex CLI (codex)' },
  { value: 'opencode', label: 'OpenCode (opencode)' },
  { value: 'custom_shell', label: 'Shell Local (custom_shell)' }
];

export const ROLE_OPTIONS = [
  { value: 'implementer', label: 'Implementer' },
  { value: 'reviewer', label: 'Reviewer' },
  { value: 'auditor', label: 'Auditor' },
  { value: 'coordinator', label: 'Coordinator' }
];

export const LAYOUT_OPTIONS = [
  { id: '1x1', label: '1x1', count: 1, columns: '1fr' },
  { id: '2x1', label: '2x1', count: 2, columns: 'repeat(2, minmax(0, 1fr))' },
  { id: '2x2', label: '2x2', count: 4, columns: 'repeat(2, minmax(0, 1fr))' }
];

const SIZE_PRESETS = [
  { label: '80x24', cols: 80, rows: 24 },
  { label: '100x28', cols: 100, rows: 28 },
  { label: '120x36', cols: 120, rows: 36 }
];

const DEFAULT_MANUAL_COMMAND =
  'mcp2p --mcp-server --transport streamable-http --host 127.0.0.1 --port 8765';

const DEFAULT_MCP_CONFIG = {
  mcpServers: {
    mcp2p: {
      url: 'http://127.0.0.1:8765/mcp'
    }
  }
};

const POLL_INTERVAL_MS = 350;
const MAX_OUTPUT_CHARS = 150000;
const ANSI_ESCAPE_REGEX = /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g;
const URL_SPLIT_REGEX = /(https?:\/\/[^\s<>"'`)\]]+)/g;

function createInitialPaneState(paneId, index) {
  return {
    paneId,
    label: `Pane ${index + 1}`,
    cliProfile: 'claude',
    role: 'implementer',
    cols: 100,
    rows: 28,
    alive: false,
    paused: false,
    pid: null,
    rssMb: 0,
    nodeUuid: '',
    sessionId: '',
    exitCode: null,
    output: '',
    busy: false,
    error: null,
    degradedInfo: null
  };
}

function createInitialPanesMap() {
  const map = {};
  MATRIX_PANE_IDS.forEach((paneId, idx) => {
    map[paneId] = createInitialPaneState(paneId, idx);
  });
  return map;
}

export function stripAnsiSequences(text) {
  if (!text) return '';
  return String(text).replace(ANSI_ESCAPE_REGEX, '');
}

function splitUrlTrailingPunctuation(rawUrl) {
  let clean = rawUrl;
  let trailing = '';
  while (clean.length > 0 && /[.,;:!?]/.test(clean[clean.length - 1])) {
    trailing = clean[clean.length - 1] + trailing;
    clean = clean.slice(0, -1);
  }
  return { clean, trailing };
}

function renderOutputWithSafeLinks(text, onRequestOpenUrl) {
  const cleaned = stripAnsiSequences(text);
  if (!cleaned) return null;
  const parts = cleaned.split(URL_SPLIT_REGEX);
  return parts.map((part, idx) => {
    if (!part) return null;
    if (/^https?:\/\//i.test(part)) {
      const { clean, trailing } = splitUrlTrailingPunctuation(part);
      if (!clean) return part;
      return (
        <React.Fragment key={`url-${idx}`}>
          <button
            type="button"
            data-testid="tty-detected-url"
            onClick={(e) => {
              e.stopPropagation();
              onRequestOpenUrl(clean);
            }}
            title={`Abrir enlace externo con confirmación: ${clean}`}
            style={{
              display: 'inline',
              border: 'none',
              background: 'transparent',
              padding: 0,
              margin: 0,
              font: 'inherit',
              color: 'var(--accent-primary)',
              textDecoration: 'underline',
              cursor: 'pointer'
            }}
          >
            {clean}
          </button>
          {trailing}
        </React.Fragment>
      );
    }
    return <React.Fragment key={`txt-${idx}`}>{part}</React.Fragment>;
  });
}

function getPaneStatusMeta(pane) {
  if (pane.degradedInfo) {
    return {
      label: 'Degradado',
      color: 'var(--accent-warning)',
      bg: 'rgba(245, 158, 11, 0.14)',
      border: 'rgba(245, 158, 11, 0.35)'
    };
  }
  if (pane.alive && pane.paused) {
    return {
      label: 'Pausado',
      color: 'var(--accent-warning)',
      bg: 'rgba(245, 158, 11, 0.14)',
      border: 'rgba(245, 158, 11, 0.35)'
    };
  }
  if (pane.alive) {
    return {
      label: 'Activo',
      color: 'var(--accent-success)',
      bg: 'rgba(var(--accent-primary-rgb), 0.14)',
      border: 'rgba(var(--accent-primary-rgb), 0.35)'
    };
  }
  return {
    label: 'Libre',
    color: 'var(--text-muted)',
    bg: 'var(--bg-subtle)',
    border: 'var(--border-subtle)'
  };
}

function DegradedManualCard({
  testId,
  title,
  message,
  code,
  manualCommand,
  mcpConfig,
  copiedKey,
  onCopy
}) {
  const cmdText = manualCommand || DEFAULT_MANUAL_COMMAND;
  const configText = JSON.stringify(mcpConfig || DEFAULT_MCP_CONFIG, null, 2);
  const cmdKey = `${testId}-cmd`;
  const cfgKey = `${testId}-cfg`;

  return (
    <div
      data-testid={testId}
      style={{
        backgroundColor: 'rgba(245, 158, 11, 0.08)',
        border: '1px solid rgba(245, 158, 11, 0.38)',
        borderRadius: 'var(--radius-md)',
        padding: '10px 12px',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px'
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', fontWeight: 700, color: 'var(--accent-warning)' }}>
          <AlertTriangle size={14} />
          <span>{title || 'Modo Degradado (RF-STM-12)'}</span>
        </div>
        {code && (
          <span style={{
            fontFamily: 'var(--font-mono)',
            fontSize: '10.5px',
            padding: '1px 6px',
            borderRadius: 'var(--radius-sm)',
            backgroundColor: 'rgba(245, 158, 11, 0.16)',
            color: 'var(--accent-warning)'
          }}>
            {code}
          </span>
        )}
      </div>

      {message && (
        <p style={{ margin: 0, fontSize: '11.5px', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
          {message}
        </p>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
          <span style={{ fontSize: '10.5px', fontWeight: 600, color: 'var(--text-secondary)' }}>
            Comando CLI Manual
          </span>
          <button
            type="button"
            data-testid={`${testId}-copy-cmd`}
            onClick={() => onCopy(cmdKey, cmdText)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              padding: '2px 7px',
              fontSize: '10.5px',
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-surface)',
              color: copiedKey === cmdKey ? 'var(--accent-success)' : 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            {copiedKey === cmdKey ? <Check size={11} /> : <Copy size={11} />}
            <span>{copiedKey === cmdKey ? 'Copiado' : 'Copiar comando'}</span>
          </button>
        </div>
        <pre style={{
          margin: 0,
          padding: '6px 8px',
          borderRadius: 'var(--radius-sm)',
          backgroundColor: 'var(--bg-app)',
          border: '1px solid var(--border-subtle)',
          fontFamily: 'var(--font-mono)',
          fontSize: '11px',
          color: 'var(--text-primary)',
          overflowX: 'auto',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
          userSelect: 'text'
        }}>
          {cmdText}
        </pre>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
          <span style={{ fontSize: '10.5px', fontWeight: 600, color: 'var(--text-secondary)' }}>
            Configuración JSON (mcpServers)
          </span>
          <button
            type="button"
            data-testid={`${testId}-copy-config`}
            onClick={() => onCopy(cfgKey, configText)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '4px',
              padding: '2px 7px',
              fontSize: '10.5px',
              fontWeight: 600,
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-surface)',
              color: copiedKey === cfgKey ? 'var(--accent-success)' : 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            {copiedKey === cfgKey ? <Check size={11} /> : <Copy size={11} />}
            <span>{copiedKey === cfgKey ? 'Copiado' : 'Copiar JSON'}</span>
          </button>
        </div>
        <pre style={{
          margin: 0,
          padding: '6px 8px',
          borderRadius: 'var(--radius-sm)',
          backgroundColor: 'var(--bg-app)',
          border: '1px solid var(--border-subtle)',
          fontFamily: 'var(--font-mono)',
          fontSize: '10.5px',
          color: 'var(--text-primary)',
          overflowX: 'auto',
          userSelect: 'text'
        }}>
          {configText}
        </pre>
      </div>
    </div>
  );
}

export function SwarmTerminalMatrix({ api, project, searchQuery = '' }) {
  const [layout, setLayout] = useState('2x2');
  const [panes, setPanes] = useState(() => createInitialPanesMap());
  const [inputs, setInputs] = useState({
    pane_1: '',
    pane_2: '',
    pane_3: '',
    pane_4: ''
  });
  const [focusedPaneId, setFocusedPaneId] = useState('pane_1');
  const [matrixStatus, setMatrixStatus] = useState({
    degraded: false,
    hubUrl: null,
    hubRunning: false,
    manualCommand: null,
    mcpConfig: null,
    loading: false
  });
  const [showManualInstructions, setShowManualInstructions] = useState(false);
  const [copiedKey, setCopiedKey] = useState(null);
  const [pendingExternalUrl, setPendingExternalUrl] = useState(null);

  const mountedRef = useRef(true);
  const nextSeqRef = useRef({ pane_1: 0, pane_2: 0, pane_3: 0, pane_4: 0 });
  const pollingInFlightRef = useRef({ pane_1: false, pane_2: false, pane_3: false, pane_4: false });
  const inputRefs = useRef({});
  const terminalRefs = useRef({});

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
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

  const refreshMatrixStatus = useCallback(async () => {
    if (!api?.pty_get_matrix_status) return;
    setMatrixStatus((prev) => ({ ...prev, loading: true }));
    try {
      const res = await api.pty_get_matrix_status();
      if (!mountedRef.current || !res) return;

      const isDegraded = Boolean(res.degraded);
      setMatrixStatus({
        degraded: isDegraded,
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
          if (s) {
            next[paneId] = {
              ...cur,
              cliProfile: s.cli_profile || r?.cli_profile || cur.cliProfile,
              role: s.role || r?.role || cur.role,
              cols: s.cols || cur.cols,
              rows: s.rows || cur.rows,
              alive: Boolean(s.alive),
              paused: Boolean(s.paused),
              pid: s.pid ?? r?.pid ?? cur.pid,
              rssMb: typeof s.rss_mb === 'number' ? s.rss_mb : cur.rssMb,
              nodeUuid: s.node_uuid || r?.node_uuid || cur.nodeUuid,
              sessionId: s.session_id || cur.sessionId,
              degradedInfo: s.alive ? null : cur.degradedInfo
            };
          } else if (cur.alive) {
            next[paneId] = {
              ...cur,
              alive: false,
              paused: false,
              rssMb: 0
            };
          } else if (r) {
            next[paneId] = {
              ...cur,
              nodeUuid: r.node_uuid || cur.nodeUuid,
              pid: r.pid ?? cur.pid
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

  useEffect(() => {
    refreshMatrixStatus();
  }, [refreshMatrixStatus, project?.project_uuid]);

  const pollPaneOnce = useCallback(async (paneId) => {
    if (!api?.pty_poll_output || pollingInFlightRef.current[paneId]) return;
    pollingInFlightRef.current[paneId] = true;
    const cursor = nextSeqRef.current[paneId] ?? 0;
    try {
      const res = await api.pty_poll_output(paneId, cursor);
      if (!mountedRef.current || !res) return;

      if (typeof res.next_seq === 'number') {
        nextSeqRef.current[paneId] = res.next_seq;
      }

      setPanes((prev) => {
        const cur = prev[paneId];
        if (!cur) return prev;
        let nextOutput = cur.output;
        if (res.truncated) {
          nextOutput += '\n[...salida anterior truncada del buffer...]\n';
        }
        if (res.text) {
          nextOutput += res.text;
          if (nextOutput.length > MAX_OUTPUT_CHARS) {
            nextOutput = nextOutput.slice(-MAX_OUTPUT_CHARS);
          }
        }
        const stillAlive = Boolean(res.alive);
        return {
          ...prev,
          [paneId]: {
            ...cur,
            output: nextOutput,
            alive: stillAlive,
            paused: Boolean(res.paused),
            rssMb: stillAlive ? (typeof res.rss_mb === 'number' ? res.rss_mb : cur.rssMb) : 0,
            exitCode: res.exit_code !== undefined ? res.exit_code : cur.exitCode,
            degradedInfo: res.degraded
              ? {
                  code: res.code || 'TERMINAL_ADAPTER_DEGRADED',
                  message: res.message || res.error || 'Modo degradado activo',
                  manualCommand: res.manual_command,
                  mcpConfig: res.mcp_config
                }
              : cur.degradedInfo
          }
        };
      });

      const el = terminalRefs.current[paneId];
      if (el && res.text) {
        el.scrollTop = el.scrollHeight;
      }
    } catch {
      // No romper el ciclo de polling por un fallo transitorio
    } finally {
      pollingInFlightRef.current[paneId] = false;
    }
  }, [api]);

  const alivePaneIdsKey = MATRIX_PANE_IDS.filter((id) => panes[id].alive).join(',');

  useEffect(() => {
    if (!alivePaneIdsKey || !api?.pty_poll_output) return undefined;
    const activeIds = alivePaneIdsKey.split(',').filter(Boolean);
    if (!activeIds.length) return undefined;

    activeIds.forEach((id) => {
      void pollPaneOnce(id);
    });

    const timer = setInterval(() => {
      activeIds.forEach((id) => {
        void pollPaneOnce(id);
      });
    }, POLL_INTERVAL_MS);

    return () => clearInterval(timer);
  }, [alivePaneIdsKey, api, pollPaneOnce]);

  const handleSpawnPane = async (paneId) => {
    if (!api?.pty_spawn_pane) return;
    const pane = panes[paneId];
    if (!pane || pane.busy) return;

    nextSeqRef.current[paneId] = 0;
    setPanes((prev) => ({
      ...prev,
      [paneId]: {
        ...prev[paneId],
        busy: true,
        error: null,
        degradedInfo: null,
        exitCode: null,
        output: ''
      }
    }));

    try {
      const res = await api.pty_spawn_pane(
        paneId,
        pane.cliProfile,
        pane.role,
        null,
        pane.cols,
        pane.rows,
        true
      );
      if (!mountedRef.current) return;

      if (res?.hub_url) {
        setMatrixStatus((prev) => ({
          ...prev,
          hubUrl: res.hub_url,
          hubRunning: true
        }));
      }

      if (res?.degraded || !res?.success) {
        const degraded = Boolean(res?.degraded);
        setPanes((prev) => ({
          ...prev,
          [paneId]: {
            ...prev[paneId],
            busy: false,
            alive: false,
            paused: false,
            pid: null,
            rssMb: 0,
            error: !degraded ? (res?.message || res?.error || 'No se pudo iniciar la sesión') : null,
            degradedInfo: degraded
              ? {
                  code: res?.code || 'TERMINAL_ADAPTER_DEGRADED',
                  message: res?.message || res?.error || 'Adaptador PTY no disponible. Usa conexión manual.',
                  manualCommand: res?.manual_command || DEFAULT_MANUAL_COMMAND,
                  mcpConfig: res?.mcp_config || DEFAULT_MCP_CONFIG
                }
              : null
          }
        }));
        if (degraded && res?.code !== 'CLI_BINARY_NOT_FOUND') {
          setMatrixStatus((prev) => ({
            ...prev,
            degraded: true,
            manualCommand: res?.manual_command || prev.manualCommand || DEFAULT_MANUAL_COMMAND,
            mcpConfig: res?.mcp_config || prev.mcpConfig || DEFAULT_MCP_CONFIG
          }));
        }
        return;
      }

      const sess = res.session || {};
      const paneMeta = res.pane || {};
      setPanes((prev) => ({
        ...prev,
        [paneId]: {
          ...prev[paneId],
          busy: false,
          alive: true,
          paused: Boolean(sess.paused),
          pid: res.pid ?? sess.pid ?? paneMeta.pid ?? null,
          rssMb: typeof sess.rss_mb === 'number' ? sess.rss_mb : 0,
          nodeUuid: res.node_uuid || paneMeta.node_uuid || sess.node_uuid || '',
          sessionId: sess.session_id || '',
          error: null,
          degradedInfo: null
        }
      }));
      setFocusedPaneId(paneId);
    } catch (err) {
      if (!mountedRef.current) return;
      setPanes((prev) => ({
        ...prev,
        [paneId]: {
          ...prev[paneId],
          busy: false,
          alive: false,
          error: err?.message || String(err)
        }
      }));
    }
  };

  const handlePausePane = async (paneId) => {
    if (!api?.pty_pause_pane) return;
    const pane = panes[paneId];
    if (!pane || !pane.alive) return;
    const targetPaused = !pane.paused;
    try {
      const res = await api.pty_pause_pane(paneId, targetPaused);
      if (!mountedRef.current) return;
      if (res?.success) {
        setPanes((prev) => ({
          ...prev,
          [paneId]: {
            ...prev[paneId],
            paused: typeof res.paused === 'boolean' ? res.paused : targetPaused
          }
        }));
      } else if (res?.message || res?.error) {
        setPanes((prev) => ({
          ...prev,
          [paneId]: { ...prev[paneId], error: res.message || res.error }
        }));
      }
    } catch (err) {
      if (mountedRef.current) {
        setPanes((prev) => ({
          ...prev,
          [paneId]: { ...prev[paneId], error: err?.message || String(err) }
        }));
      }
    }
  };

  const handleClosePane = async (paneId) => {
    const pane = panes[paneId];
    if (!pane) return;
    try {
      if (api?.pty_close_pane && (pane.alive || pane.sessionId)) {
        await api.pty_close_pane(paneId);
      }
    } catch {
      // Limpiar estado visual aunque el proceso ya haya salido
    }
    if (!mountedRef.current) return;
    nextSeqRef.current[paneId] = 0;
    setPanes((prev) => ({
      ...prev,
      [paneId]: {
        ...prev[paneId],
        alive: false,
        paused: false,
        pid: null,
        rssMb: 0,
        nodeUuid: '',
        sessionId: '',
        exitCode: null,
        error: null,
        degradedInfo: null
      }
    }));
  };

  const handleResizePane = async (paneId, cols, rows) => {
    setPanes((prev) => ({
      ...prev,
      [paneId]: { ...prev[paneId], cols, rows }
    }));
    const pane = panes[paneId];
    if (pane?.alive && api?.pty_resize_pane) {
      try {
        await api.pty_resize_pane(paneId, cols, rows);
      } catch {
        // Ignorar fallo transitorio de redimensionado
      }
    }
  };

  const sendPaneInput = async (paneId, payload) => {
    if (!api?.pty_write_input || !payload) return;
    try {
      const res = await api.pty_write_input(paneId, payload, true);
      if (mountedRef.current && res && res.success === false && (res.message || res.error)) {
        setPanes((prev) => ({
          ...prev,
          [paneId]: { ...prev[paneId], error: res.message || res.error }
        }));
      }
    } catch (err) {
      if (mountedRef.current) {
        setPanes((prev) => ({
          ...prev,
          [paneId]: { ...prev[paneId], error: err?.message || String(err) }
        }));
      }
    }
  };

  const handleSubmitCommand = async (paneId, e) => {
    e.preventDefault();
    const pane = panes[paneId];
    if (!pane?.alive) return;
    const raw = inputs[paneId] ?? '';
    const payload = raw
      ? (raw.endsWith('\n') || raw.endsWith('\r') ? raw : `${raw}\n`)
      : '\n';
    setInputs((prev) => ({ ...prev, [paneId]: '' }));
    await sendPaneInput(paneId, payload);
  };

  const handleTerminalKeyDown = (paneId, e) => {
    const pane = panes[paneId];
    if (!pane?.alive) return;

    if (e.ctrlKey && !e.altKey && !e.metaKey && e.key.toLowerCase() === 'c') {
      const sel = typeof window !== 'undefined' ? window.getSelection?.()?.toString() : '';
      if (!sel) {
        e.preventDefault();
        void sendPaneInput(paneId, '\x03');
      }
      return;
    }

    let payload = null;
    if (e.key === 'Enter') payload = '\r';
    else if (e.key === 'Backspace') payload = '\x7f';
    else if (e.key === 'Tab') payload = '\t';
    else if (e.key === 'Escape') payload = '\x1b';
    else if (e.key === 'ArrowUp') payload = '\x1b[A';
    else if (e.key === 'ArrowDown') payload = '\x1b[B';
    else if (e.key === 'ArrowRight') payload = '\x1b[C';
    else if (e.key === 'ArrowLeft') payload = '\x1b[D';
    else if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) {
      payload = e.key;
    }

    if (payload !== null) {
      e.preventDefault();
      void sendPaneInput(paneId, payload);
    }
  };

  const handleFocusPane = (paneId) => {
    setFocusedPaneId(paneId);
    const inputEl = inputRefs.current[paneId];
    const termEl = terminalRefs.current[paneId];
    if (inputEl && !inputEl.disabled) {
      inputEl.focus();
    } else if (termEl) {
      termEl.focus();
    }
  };

  const currentLayout = LAYOUT_OPTIONS.find((opt) => opt.id === layout) || LAYOUT_OPTIONS[2];
  const visiblePaneIds = MATRIX_PANE_IDS.slice(0, currentLayout.count);
  const normalizedSearch = (searchQuery || '').trim().toLowerCase();
  const filteredPaneIds = normalizedSearch
    ? visiblePaneIds.filter((paneId) => {
        const p = panes[paneId];
        const haystack = [
          p.paneId,
          p.label,
          p.cliProfile,
          p.role,
          p.nodeUuid,
          p.output,
          p.degradedInfo?.code
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();
        return haystack.includes(normalizedSearch);
      })
    : visiblePaneIds;

  const isBannerVisible = matrixStatus.degraded || showManualInstructions;

  return (
    <div
      data-testid="swarm-terminal-matrix"
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        width: '100%',
        gap: '12px',
        minHeight: 0,
        overflow: 'hidden'
      }}
    >
      {/* Barra superior de la matriz */}
      <div
        data-testid="matrix-header-bar"
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '10px',
          padding: '10px 14px',
          backgroundColor: 'var(--bg-card)',
          border: '1px solid var(--border-subtle)',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-sm)',
          flexShrink: 0
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
          <div style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)' }}>
            <Terminal size={15} color="var(--accent-primary)" />
            <span>Swarm Terminal Matrix</span>
          </div>

          {/* Selector de layout 1x1 / 2x1 / 2x2 */}
          <div
            role="group"
            aria-label="Layout de matriz"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '3px',
              padding: '2px',
              borderRadius: 'var(--radius-md)',
              backgroundColor: 'var(--bg-subtle)',
              border: '1px solid var(--border-subtle)'
            }}
          >
            {LAYOUT_OPTIONS.map((opt) => {
              const active = layout === opt.id;
              return (
                <button
                  key={opt.id}
                  type="button"
                  data-testid={`layout-btn-${opt.id}`}
                  aria-pressed={active}
                  onClick={() => setLayout(opt.id)}
                  style={{
                    padding: '4px 9px',
                    fontSize: '11.5px',
                    fontWeight: 700,
                    fontFamily: 'var(--font-mono)',
                    borderRadius: 'var(--radius-sm)',
                    border: 'none',
                    backgroundColor: active ? 'var(--accent-primary)' : 'transparent',
                    color: active ? '#ffffff' : 'var(--text-secondary)',
                    cursor: 'pointer',
                    transition: 'var(--transition)'
                  }}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>

          {/* Indicador de estado EmbeddedMcpHub */}
          <div
            data-testid="hub-status-badge"
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              padding: '4px 10px',
              borderRadius: 'var(--radius-full)',
              fontSize: '11.5px',
              fontWeight: 600,
              fontFamily: 'var(--font-mono)',
              backgroundColor: matrixStatus.degraded
                ? 'rgba(245, 158, 11, 0.14)'
                : matrixStatus.hubRunning
                  ? 'rgba(var(--accent-primary-rgb), 0.14)'
                  : 'var(--bg-subtle)',
              color: matrixStatus.degraded
                ? 'var(--accent-warning)'
                : matrixStatus.hubRunning
                  ? 'var(--accent-success)'
                  : 'var(--text-secondary)',
              border: `1px solid ${
                matrixStatus.degraded
                  ? 'rgba(245, 158, 11, 0.35)'
                  : matrixStatus.hubRunning
                    ? 'rgba(var(--accent-primary-rgb), 0.35)'
                    : 'var(--border-subtle)'
              }`
            }}
          >
            <span
              style={{
                width: '7px',
                height: '7px',
                borderRadius: '50%',
                backgroundColor: matrixStatus.degraded
                  ? 'var(--accent-warning)'
                  : matrixStatus.hubRunning
                    ? 'var(--accent-success)'
                    : 'var(--text-muted)'
              }}
            />
            <span>
              {matrixStatus.degraded
                ? 'Degradado'
                : matrixStatus.hubRunning && matrixStatus.hubUrl
                  ? `Hub Activo: ${matrixStatus.hubUrl}`
                  : 'Hub en espera'}
            </span>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <button
            type="button"
            data-testid="toggle-manual-instructions-btn"
            aria-pressed={isBannerVisible}
            onClick={() => setShowManualInstructions((prev) => !prev)}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '5px 10px',
              fontSize: '11.5px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: isBannerVisible ? 'rgba(245, 158, 11, 0.12)' : 'var(--bg-subtle)',
              color: isBannerVisible ? 'var(--accent-warning)' : 'var(--text-secondary)',
              cursor: 'pointer'
            }}
          >
            <AlertTriangle size={13} />
            <span>RF-STM-12 Manual</span>
          </button>

          <button
            type="button"
            data-testid="matrix-refresh-btn"
            onClick={refreshMatrixStatus}
            disabled={matrixStatus.loading}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              padding: '5px 11px',
              fontSize: '11.5px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              cursor: matrixStatus.loading ? 'wait' : 'pointer'
            }}
          >
            <RefreshCw size={13} />
            <span>Actualizar Estado</span>
          </button>
        </div>
      </div>

      {/* Banner global de alerta RF-STM-12 */}
      {isBannerVisible && (
        <DegradedManualCard
          testId="matrix-degraded-banner"
          title="Modo Degradado / Conexión Manual CLI (RF-STM-12)"
          message={
            matrixStatus.degraded
              ? 'El backend PTY nativo no está disponible o se encuentra en circuito abierto. Conecta un cliente CLI externo manualmente al servidor MCP:'
              : 'Instrucciones de conexión manual CLI para agentes externos (RF-STM-12):'
          }
          code={matrixStatus.degraded ? 'TERMINAL_ADAPTER_DEGRADED' : 'MANUAL_CLI'}
          manualCommand={matrixStatus.manualCommand || DEFAULT_MANUAL_COMMAND}
          mcpConfig={matrixStatus.mcpConfig || DEFAULT_MCP_CONFIG}
          copiedKey={copiedKey}
          onCopy={handleCopy}
        />
      )}

      {/* Grid de panes */}
      <div
        data-testid="matrix-grid"
        style={{
          flex: 1,
          minHeight: 0,
          display: 'grid',
          gridTemplateColumns: currentLayout.columns,
          gridAutoRows: currentLayout.count <= 2 ? 'minmax(0, 1fr)' : 'minmax(240px, 1fr)',
          gap: '12px',
          overflowY: 'auto',
          paddingRight: '2px'
        }}
      >
        {filteredPaneIds.length === 0 ? (
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
            Ningún panel coincide con «{searchQuery}».
          </div>
        ) : (
          filteredPaneIds.map((paneId) => {
            const pane = panes[paneId];
            const statusMeta = getPaneStatusMeta(pane);
            const isFocused = focusedPaneId === paneId;
            const currentSizeValue = `${pane.cols}x${pane.rows}`;
            const hasKnownPreset = SIZE_PRESETS.some((p) => p.label === currentSizeValue);

            return (
              <section
                key={paneId}
                data-testid={`terminal-pane-${paneId}`}
                aria-label={pane.label}
                onClick={() => setFocusedPaneId(paneId)}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  minHeight: 0,
                  backgroundColor: 'var(--bg-card)',
                  border: `1px solid ${isFocused ? 'var(--accent-primary)' : 'var(--border-subtle)'}`,
                  borderRadius: 'var(--radius-lg)',
                  boxShadow: 'var(--shadow-sm)',
                  overflow: 'hidden'
                }}
              >
                {/* Cabecera del pane */}
                <div
                  style={{
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '7px',
                    padding: '9px 11px',
                    backgroundColor: 'var(--bg-surface)',
                    borderBottom: '1px solid var(--border-subtle)',
                    flexShrink: 0
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', flexWrap: 'wrap' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '7px', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: '12.5px', fontWeight: 700, color: 'var(--text-primary)' }}>
                        {pane.label}
                      </span>
                      <span
                        data-testid={`pane-status-${paneId}`}
                        style={{
                          padding: '1px 8px',
                          borderRadius: 'var(--radius-full)',
                          fontSize: '10.5px',
                          fontWeight: 700,
                          backgroundColor: statusMeta.bg,
                          color: statusMeta.color,
                          border: `1px solid ${statusMeta.border}`
                        }}
                      >
                        {statusMeta.label}
                      </span>
                      {pane.pid && (
                        <span
                          data-testid={`pane-pid-${paneId}`}
                          style={{
                            padding: '1px 6px',
                            borderRadius: 'var(--radius-sm)',
                            fontFamily: 'var(--font-mono)',
                            fontSize: '10px',
                            backgroundColor: 'var(--bg-subtle)',
                            color: 'var(--text-secondary)',
                            border: '1px solid var(--border-subtle)'
                          }}
                        >
                          PID {pane.pid}
                        </span>
                      )}
                      {(pane.alive || pane.rssMb > 0) && (
                        <span
                          data-testid={`pane-rss-${paneId}`}
                          style={{
                            padding: '1px 6px',
                            borderRadius: 'var(--radius-sm)',
                            fontFamily: 'var(--font-mono)',
                            fontSize: '10px',
                            backgroundColor: 'var(--bg-subtle)',
                            color: 'var(--text-secondary)',
                            border: '1px solid var(--border-subtle)'
                          }}
                        >
                          {Number(pane.rssMb || 0).toFixed(1)} MB
                        </span>
                      )}
                      {pane.nodeUuid && (
                        <span
                          data-testid={`pane-node-${paneId}`}
                          title={pane.nodeUuid}
                          style={{
                            padding: '1px 6px',
                            borderRadius: 'var(--radius-sm)',
                            fontFamily: 'var(--font-mono)',
                            fontSize: '10px',
                            backgroundColor: 'rgba(var(--accent-indigo-rgb), 0.14)',
                            color: 'var(--accent-indigo)',
                            border: '1px solid rgba(var(--accent-indigo-rgb), 0.28)'
                          }}
                        >
                          {pane.nodeUuid.slice(0, 12)}
                        </span>
                      )}
                    </div>

                    {/* Botones de acción del pane */}
                    <div style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', flexWrap: 'wrap' }}>
                      {!pane.alive ? (
                        <button
                          type="button"
                          data-testid={`pane-start-btn-${paneId}`}
                          disabled={pane.busy}
                          onClick={(e) => {
                            e.stopPropagation();
                            void handleSpawnPane(paneId);
                          }}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '4px',
                            padding: '4px 9px',
                            fontSize: '11px',
                            fontWeight: 700,
                            borderRadius: 'var(--radius-sm)',
                            border: 'none',
                            backgroundColor: 'var(--accent-primary)',
                            color: '#ffffff',
                            cursor: pane.busy ? 'wait' : 'pointer'
                          }}
                        >
                          <Play size={11} />
                          <span>{pane.busy ? 'Iniciando...' : 'Iniciar'}</span>
                        </button>
                      ) : (
                        <button
                          type="button"
                          data-testid={`pane-pause-btn-${paneId}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            void handlePausePane(paneId);
                          }}
                          style={{
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: '4px',
                            padding: '4px 9px',
                            fontSize: '11px',
                            fontWeight: 600,
                            borderRadius: 'var(--radius-sm)',
                            border: '1px solid var(--border-subtle)',
                            backgroundColor: 'var(--bg-subtle)',
                            color: 'var(--text-primary)',
                            cursor: 'pointer'
                          }}
                        >
                          {pane.paused ? <Play size={11} /> : <Pause size={11} />}
                          <span>{pane.paused ? 'Reanudar' : 'Pausar'}</span>
                        </button>
                      )}

                      <button
                        type="button"
                        data-testid={`pane-close-btn-${paneId}`}
                        disabled={!pane.alive && !pane.degradedInfo && !pane.output && pane.exitCode === null}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleClosePane(paneId);
                        }}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '4px',
                          padding: '4px 8px',
                          fontSize: '11px',
                          fontWeight: 600,
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid var(--border-subtle)',
                          backgroundColor: 'var(--bg-subtle)',
                          color: pane.alive ? 'var(--accent-danger)' : 'var(--text-secondary)',
                          cursor: !pane.alive && !pane.degradedInfo && !pane.output && pane.exitCode === null ? 'not-allowed' : 'pointer',
                          opacity: !pane.alive && !pane.degradedInfo && !pane.output && pane.exitCode === null ? 0.5 : 1
                        }}
                      >
                        <Square size={10} />
                        <span>Cerrar</span>
                      </button>

                      <button
                        type="button"
                        data-testid={`pane-focus-btn-${paneId}`}
                        title="Enfocar terminal"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleFocusPane(paneId);
                        }}
                        style={{
                          display: 'inline-flex',
                          alignItems: 'center',
                          gap: '3px',
                          padding: '4px 7px',
                          fontSize: '11px',
                          fontWeight: 600,
                          borderRadius: 'var(--radius-sm)',
                          border: '1px solid var(--border-subtle)',
                          backgroundColor: isFocused ? 'rgba(var(--accent-primary-rgb), 0.12)' : 'transparent',
                          color: isFocused ? 'var(--accent-primary)' : 'var(--text-muted)',
                          cursor: 'pointer'
                        }}
                      >
                        <Focus size={11} />
                        <span>Foco</span>
                      </button>
                    </div>
                  </div>

                  {/* Selectores de perfil CLI, rol y tamaño rápido */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
                    <select
                      aria-label={`Perfil CLI ${pane.label}`}
                      data-testid={`cli-profile-${paneId}`}
                      className="mcp-combobox"
                      disabled={pane.alive}
                      value={pane.cliProfile}
                      onChange={(e) => {
                        const val = e.target.value;
                        setPanes((prev) => ({
                          ...prev,
                          [paneId]: { ...prev[paneId], cliProfile: val }
                        }));
                      }}
                      style={{ height: '25px', fontSize: '11px', flex: '1 1 130px' }}
                    >
                      {CLI_PROFILE_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>

                    <select
                      aria-label={`Rol ${pane.label}`}
                      data-testid={`role-select-${paneId}`}
                      className="mcp-combobox"
                      disabled={pane.alive}
                      value={pane.role}
                      onChange={(e) => {
                        const val = e.target.value;
                        setPanes((prev) => ({
                          ...prev,
                          [paneId]: { ...prev[paneId], role: val }
                        }));
                      }}
                      style={{ height: '25px', fontSize: '11px', flex: '1 1 110px' }}
                    >
                      {ROLE_OPTIONS.map((opt) => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>

                    <select
                      aria-label={`Tamaño PTY ${pane.label}`}
                      data-testid={`size-select-${paneId}`}
                      className="mcp-combobox"
                      value={currentSizeValue}
                      onChange={(e) => {
                        const [c, r] = e.target.value.split('x').map((n) => parseInt(n, 10));
                        if (c > 0 && r > 0) {
                          void handleResizePane(paneId, c, r);
                        }
                      }}
                      style={{ height: '25px', fontSize: '11px', maxWidth: '92px' }}
                    >
                      {!hasKnownPreset && <option value={currentSizeValue}>{currentSizeValue}</option>}
                      {SIZE_PRESETS.map((preset) => (
                        <option key={preset.label} value={preset.label}>{preset.label}</option>
                      ))}
                    </select>
                  </div>
                </div>

                {/* Cuerpo del pane: tarjeta degradada RF-STM-12 o consola PTY */}
                <div
                  style={{
                    flex: 1,
                    minHeight: 0,
                    display: 'flex',
                    flexDirection: 'column',
                    padding: '8px 10px',
                    gap: '8px',
                    overflow: 'hidden'
                  }}
                >
                  {pane.error && (
                    <div
                      role="alert"
                      style={{
                        padding: '6px 9px',
                        borderRadius: 'var(--radius-sm)',
                        backgroundColor: 'rgba(239, 68, 68, 0.12)',
                        border: '1px solid rgba(239, 68, 68, 0.35)',
                        color: 'var(--accent-danger)',
                        fontSize: '11.5px'
                      }}
                    >
                      {pane.error}
                    </div>
                  )}

                  {pane.degradedInfo && (
                    <div style={{ overflowY: 'auto' }}>
                      <DegradedManualCard
                        testId={`pane-degraded-card-${paneId}`}
                        title={`RF-STM-12 · ${pane.label} en Modo Degradado`}
                        message={pane.degradedInfo.message}
                        code={pane.degradedInfo.code}
                        manualCommand={pane.degradedInfo.manualCommand}
                        mcpConfig={pane.degradedInfo.mcpConfig}
                        copiedKey={copiedKey}
                        onCopy={handleCopy}
                      />
                    </div>
                  )}

                  <pre
                    ref={(el) => { terminalRefs.current[paneId] = el; }}
                    data-testid={`pane-output-${paneId}`}
                    tabIndex={0}
                    onKeyDown={(e) => handleTerminalKeyDown(paneId, e)}
                    style={{
                      flex: 1,
                      minHeight: '110px',
                      margin: 0,
                      padding: '8px 10px',
                      borderRadius: 'var(--radius-md)',
                      backgroundColor: 'var(--bg-app)',
                      border: '1px solid var(--border-subtle)',
                      fontFamily: 'var(--font-mono)',
                      fontSize: '11.5px',
                      lineHeight: 1.45,
                      color: 'var(--text-primary)',
                      overflowY: 'auto',
                      overflowX: 'auto',
                      whiteSpace: 'pre-wrap',
                      wordBreak: 'break-word',
                      outline: 'none',
                      userSelect: 'text'
                    }}
                  >
                    {pane.output
                      ? renderOutputWithSafeLinks(pane.output, setPendingExternalUrl)
                      : (
                        <span style={{ color: 'var(--text-muted)' }}>
                          {pane.alive
                            ? 'Sesión PTY activa. Esperando salida...'
                            : 'Panel libre. Selecciona perfil CLI/rol y pulsa Iniciar.'}
                        </span>
                      )}
                  </pre>

                  {/* Barra de entrada rápida */}
                  <form
                    onSubmit={(e) => void handleSubmitCommand(paneId, e)}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      flexShrink: 0
                    }}
                  >
                    <input
                      ref={(el) => { inputRefs.current[paneId] = el; }}
                      type="text"
                      data-testid={`pane-input-${paneId}`}
                      aria-label={`Entrada de comandos ${pane.label}`}
                      disabled={!pane.alive}
                      value={inputs[paneId] || ''}
                      onChange={(e) => setInputs((prev) => ({ ...prev, [paneId]: e.target.value }))}
                      placeholder={pane.alive ? 'Escribe comando o prompt y pulsa Enter...' : 'Inicia el panel para enviar entrada'}
                      style={{
                        flex: 1,
                        minWidth: 0,
                        height: '28px',
                        padding: '0 9px',
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--border-subtle)',
                        backgroundColor: 'var(--bg-surface)',
                        color: 'var(--text-primary)',
                        fontFamily: 'var(--font-mono)',
                        fontSize: '11.5px',
                        outline: 'none'
                      }}
                    />

                    <button
                      type="submit"
                      data-testid={`pane-send-${paneId}`}
                      disabled={!pane.alive}
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '4px',
                        height: '28px',
                        padding: '0 10px',
                        borderRadius: 'var(--radius-sm)',
                        border: 'none',
                        backgroundColor: pane.alive ? 'var(--accent-primary)' : 'var(--bg-subtle)',
                        color: pane.alive ? '#ffffff' : 'var(--text-muted)',
                        fontSize: '11px',
                        fontWeight: 700,
                        cursor: pane.alive ? 'pointer' : 'not-allowed'
                      }}
                    >
                      <Send size={11} />
                      <span>Enviar</span>
                    </button>

                    <button
                      type="button"
                      data-testid={`pane-ctrlc-${paneId}`}
                      disabled={!pane.alive}
                      title="Enviar interrupción Ctrl+C (\x03)"
                      onClick={() => void sendPaneInput(paneId, '\x03')}
                      style={{
                        height: '28px',
                        padding: '0 8px',
                        borderRadius: 'var(--radius-sm)',
                        border: '1px solid var(--border-subtle)',
                        backgroundColor: 'var(--bg-subtle)',
                        color: pane.alive ? 'var(--accent-danger)' : 'var(--text-muted)',
                        fontFamily: 'var(--font-mono)',
                        fontSize: '10.5px',
                        fontWeight: 700,
                        cursor: pane.alive ? 'pointer' : 'not-allowed'
                      }}
                    >
                      Ctrl+C
                    </button>
                  </form>
                </div>
              </section>
            );
          })
        )}
      </div>

      {/* Modal inline de confirmación explícita para URLs detectadas en salida TTY */}
      {pendingExternalUrl && (
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
                onClick={() => setPendingExternalUrl(null)}
                style={{
                  border: 'none',
                  background: 'transparent',
                  color: 'var(--text-muted)',
                  cursor: 'pointer',
                  display: 'inline-flex'
                }}
              >
                <X size={15} />
              </button>
            </div>

            <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
              Esta URL apareció en la salida TTY de un panel. Confirma explícitamente el dominio antes de abrirla para evitar aperturas involuntarias o phishing:
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
              {pendingExternalUrl}
            </pre>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
              <button
                type="button"
                data-testid="external-url-cancel-btn"
                onClick={() => setPendingExternalUrl(null)}
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
                onClick={() => {
                  const target = pendingExternalUrl;
                  setPendingExternalUrl(null);
                  if (typeof window !== 'undefined' && typeof window.open === 'function') {
                    window.open(target, '_blank', 'noopener,noreferrer');
                  }
                }}
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
      )}
    </div>
  );
}
