import React, { useEffect, useLayoutEffect, useRef } from 'react';
import { Plus } from 'lucide-react';
import { CliLogo } from './CliLogo';
import { acquireTerminal, attachTerminal, detachTerminal } from './terminalRegistry';
import './terminal.css';

// Superficie fija de terminal: negra y sin la estetica de la aplicacion, en cualquier tema.
const TERMINAL_BG = '#000000';
const TERMINAL_DIM = '#6b7280';
const TERMINAL_BORDER = '#1e1f24';

/** Debounce del redimensionado: el ResizeObserver dispara muchas veces mientras se arrastra. */
export const RESIZE_DEBOUNCE_MS = 100;

const MCP_BADGE_MAP = {
  injected: {
    label: 'MCP Hub',
    color: '#34d399',
    bg: 'rgba(16, 185, 129, 0.14)',
    border: 'rgba(16, 185, 129, 0.35)'
  },
  unsupported: {
    label: 'MCP manual',
    color: '#fbbf24',
    bg: 'rgba(245, 158, 11, 0.14)',
    border: 'rgba(245, 158, 11, 0.35)'
  },
  skipped: {
    label: 'Sin MCP',
    color: '#9ca3af',
    bg: 'rgba(156, 163, 175, 0.12)',
    border: 'rgba(156, 163, 175, 0.28)'
  }
};

const ACTIVITY_BADGE_MAP = {
  idle: {
    label: 'Libre',
    color: '#34d399',
    bg: 'rgba(16, 185, 129, 0.12)',
    border: 'rgba(16, 185, 129, 0.3)'
  },
  working: {
    label: 'Trabajando',
    color: '#60a5fa',
    bg: 'rgba(59, 130, 246, 0.14)',
    border: 'rgba(59, 130, 246, 0.35)'
  },
  blocked: {
    label: 'Permiso/Bloq',
    color: '#f87171',
    bg: 'rgba(239, 68, 68, 0.14)',
    border: 'rgba(239, 68, 68, 0.35)'
  },
  done: {
    label: 'Listo',
    color: '#a78bfa',
    bg: 'rgba(139, 92, 246, 0.14)',
    border: 'rgba(139, 92, 246, 0.35)'
  },
  paused: {
    label: 'Pausado',
    color: '#fbbf24',
    bg: 'rgba(245, 158, 11, 0.14)',
    border: 'rgba(245, 158, 11, 0.35)'
  }
};

function resolveMcpBadge(mcpInjection) {
  if (!mcpInjection) return null;
  const status = typeof mcpInjection === 'string' ? mcpInjection : mcpInjection.status;
  const spec = MCP_BADGE_MAP[status];
  if (!spec) return null;
  const details = [];
  if (typeof mcpInjection === 'object') {
    if (typeof mcpInjection.verified === 'boolean') {
      details.push(`verified=${mcpInjection.verified ? 'true' : 'false'}`);
    }
    if (mcpInjection.reason) details.push(String(mcpInjection.reason));
  }
  return {
    ...spec,
    status,
    title: details.length ? `${spec.label} (${details.join(', ')})` : spec.label
  };
}

async function copyToClipboard(text) {
  try {
    if (navigator?.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Cae al metodo clasico.
  }
  try {
    const area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(area);
    return ok;
  } catch {
    return false;
  }
}

/**
 * Atajos de portapapeles de la terminal (xterm manda a la funcion todo keydown; `false` = no
 * lo proceses). Ctrl+C con texto seleccionado lo copia; sin seleccion pasa al PTY como ^C
 * (interrumpe). Ctrl+V y Ctrl+Shift+V dejan que el navegador dispare el evento `paste`, que
 * xterm ya convierte en entrada. Todo lo demas (Esc, flechas, Ctrl+letra...) es de la terminal.
 */
export function handleTerminalKey(term, ev) {
  if (ev.type !== 'keydown') return true;
  const key = String(ev.key || '').toLowerCase();
  const mod = (ev.ctrlKey || ev.metaKey) && !ev.altKey;
  if (mod && key === 'c') {
    if (term.hasSelection()) {
      void copyToClipboard(term.getSelection());
      return false;
    }
    return !ev.shiftKey; // Ctrl+Shift+C sin seleccion no envia nada
  }
  if (mod && key === 'v') return false;
  return true;
}

/**
 * Hueco vacio de la cuadricula: solo la superficie negra. `showHint` (unico caso: no hay
 * NINGUNA terminal) deja una invitacion minima y discreta.
 */
export function EmptyTerminalSlot({ index, showHint = false, onAdd }) {
  const clickable = typeof onAdd === 'function';
  const Tag = clickable ? 'button' : 'section';
  return (
    <Tag
      type={clickable ? 'button' : undefined}
      onClick={clickable ? onAdd : undefined}
      data-testid={`terminal-pane-empty-${index}`}
      aria-label={clickable ? 'Agregar terminal' : 'Panel vacío'}
      title={clickable ? 'Agregar terminal' : undefined}
      className={clickable ? 'mcp-terminal-empty-slot is-clickable' : 'mcp-terminal-empty-slot'}
      style={{
        position: 'relative',
        minHeight: 0,
        backgroundColor: TERMINAL_BG,
        border: `1px solid ${TERMINAL_BORDER}`,
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden',
        padding: 0,
        font: 'inherit'
      }}
    >
      {showHint ? (
        <span
          data-testid="matrix-empty-hint"
          className="mcp-terminal-empty-add is-prominent"
        >
          <Plus size={16} aria-hidden="true" />
          <span>Agregar terminal</span>
        </span>
      ) : (
        clickable && (
          <span className="mcp-terminal-empty-add" aria-hidden="true">
            <Plus size={16} />
          </span>
        )
      )}
    </Tag>
  );
}

/**
 * Un panel = una terminal real (xterm). Logotipo discreto del CLI y la carpeta de trabajo
 * arriba; el resto es el emulador, con el teclado directo al PTY.
 *
 * La instancia de xterm vive en terminalRegistry (fuera de React): este componente solo la
 * cuelga en su contenedor, conecta teclado/redimensionado/enlaces mientras esta montado y la
 * suelta al desmontarse SIN destruirla. La salida la escribe el bucle de sondeo del padre.
 */
export function TerminalPane({
  pane,
  label,
  cli,
  isFocused,
  focusToken,
  onFocus,
  onInput,
  onResize,
  onRequestOpenUrl,
  registerTerminalRef
}) {
  const containerRef = useRef(null);
  const termRef = useRef(null);
  const paneId = pane.paneId;
  const sessionId = pane.sessionId;

  // Siempre la ultima version de los callbacks y del estado sin reconectar el emulador.
  const latest = useRef({});
  latest.current = { pane, onInput, onResize, onRequestOpenUrl };

  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;
    const entry = acquireTerminal(paneId, {
      sessionId,
      cols: latest.current.pane.cols,
      rows: latest.current.pane.rows
    });
    const term = entry.term;
    termRef.current = term;
    attachTerminal(entry, container);
    entry.onOpenUrl = (url) => latest.current.onRequestOpenUrl?.(url);

    // Entrada: xterm traduce teclas, pegado y secuencias; aqui solo se reenvia al PTY.
    const dataSub = term.onData((data) => {
      if (!latest.current.pane.alive) return;
      latest.current.onInput(paneId, data);
    });
    term.attachCustomKeyEventHandler((ev) => handleTerminalKey(term, ev));

    // Redimensionado: fit() al tamano del contenedor y, si cambio, avisa al backend.
    const syncSize = () => {
      if (entry.disposed) return;
      let dims;
      try {
        dims = entry.fit.proposeDimensions?.();
      } catch {
        dims = null;
      }
      if (!dims || !(dims.cols > 0) || !(dims.rows > 0)) return; // oculto o sin medir aun
      try {
        entry.fit.fit();
      } catch {
        return;
      }
      const { cols, rows } = term;
      if (cols !== entry.ptySize.cols || rows !== entry.ptySize.rows) {
        entry.ptySize = { cols, rows };
        latest.current.onResize?.(paneId, cols, rows);
      }
    };
    let debounceTimer = null;
    const scheduleSync = () => {
      if (debounceTimer !== null) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        debounceTimer = null;
        syncSize();
      }, RESIZE_DEBOUNCE_MS);
    };
    syncSize(); // primer ajuste sin esperar al debounce
    let observer = null;
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(scheduleSync);
      observer.observe(container);
    } else {
      window.addEventListener('resize', scheduleSync);
    }

    registerTerminalRef?.(paneId, { focus: () => term.focus() });

    return () => {
      dataSub?.dispose?.();
      term.attachCustomKeyEventHandler(() => true);
      if (entry.onOpenUrl) entry.onOpenUrl = null;
      if (debounceTimer !== null) clearTimeout(debounceTimer);
      if (observer) observer.disconnect();
      else window.removeEventListener('resize', scheduleSync);
      registerTerminalRef?.(paneId, null);
      termRef.current = null;
      detachTerminal(entry, container);
    };
    // La instancia solo cambia con el panel o con la sesion.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paneId, sessionId]);

  useEffect(() => {
    if (focusToken && isFocused) termRef.current?.focus();
    // Solo reacciona a una peticion nueva de foco.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusToken]);

  const logoName = cli?.display_name || pane.cliProfile;
  const mcpBadge = resolveMcpBadge(pane.mcpInjection);
  const activityBadge = pane.activityState ? ACTIVITY_BADGE_MAP[pane.activityState] || null : null;

  return (
    <section
      data-testid={`terminal-pane-${paneId}`}
      data-focused={isFocused ? 'true' : 'false'}
      aria-label={label}
      onClick={() => onFocus(paneId)}
      style={{
        position: 'relative',
        display: 'flex',
        flexDirection: 'column',
        minHeight: 0,
        minWidth: 0,
        backgroundColor: TERMINAL_BG,
        border: `1px solid ${isFocused ? 'var(--accent-primary)' : TERMINAL_BORDER}`,
        borderRadius: 'var(--radius-md)',
        boxShadow: isFocused
          ? '0 0 0 1px rgba(var(--accent-primary-rgb), 0.35), 0 0 18px rgba(var(--accent-primary-rgb), 0.28)'
          : 'none',
        transition: 'box-shadow 0.2s ease, border-color 0.2s ease',
        overflow: 'hidden'
      }}
    >
      <div
        data-testid={`pane-identity-${paneId}`}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '7px',
          padding: '6px 10px 2px',
          minWidth: 0,
          flexShrink: 0,
          pointerEvents: 'none',
          userSelect: 'none'
        }}
      >
        <span data-testid={`pane-logo-${paneId}`} data-cli={pane.cliProfile} title={logoName} style={{ display: 'inline-flex' }}>
          <CliLogo cliId={pane.cliProfile} icon={cli?.icon} name={logoName} size={13} />
        </span>
        {pane.cwd && (
          <span
            data-testid={`pane-cwd-${paneId}`}
            title={pane.cwd}
            style={{
              fontFamily: 'var(--font-mono)',
              fontSize: '10.5px',
              color: TERMINAL_DIM,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              minWidth: 0
            }}
          >
            {pane.cwd}
          </span>
        )}
        {(mcpBadge || activityBadge) && (
          <span
            style={{
              marginLeft: 'auto',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '5px',
              flexShrink: 0
            }}
          >
            {mcpBadge && (
              <span
                data-testid={`pane-mcp-badge-${paneId}`}
                data-mcp-status={mcpBadge.status}
                title={mcpBadge.title}
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '9.5px',
                  fontWeight: 600,
                  lineHeight: 1.2,
                  padding: '1px 5px',
                  borderRadius: '4px',
                  color: mcpBadge.color,
                  backgroundColor: mcpBadge.bg,
                  border: `1px solid ${mcpBadge.border}`,
                  whiteSpace: 'nowrap'
                }}
              >
                {mcpBadge.label}
              </span>
            )}
            {activityBadge && (
              <span
                data-testid={`pane-activity-badge-${paneId}`}
                data-activity-state={pane.activityState}
                title={pane.activitySource ? `Actividad (${pane.activitySource})` : 'Actividad'}
                style={{
                  fontFamily: 'var(--font-mono)',
                  fontSize: '9.5px',
                  fontWeight: 600,
                  lineHeight: 1.2,
                  padding: '1px 5px',
                  borderRadius: '4px',
                  color: activityBadge.color,
                  backgroundColor: activityBadge.bg,
                  border: `1px solid ${activityBadge.border}`,
                  whiteSpace: 'nowrap'
                }}
              >
                {activityBadge.label}
              </span>
            )}
          </span>
        )}
      </div>

      <div
        ref={containerRef}
        data-testid={`pane-output-${paneId}`}
        aria-label={`Terminal: ${label}`}
        style={{
          position: 'relative',
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          backgroundColor: TERMINAL_BG,
          overflow: 'hidden'
        }}
      />
    </section>
  );
}
