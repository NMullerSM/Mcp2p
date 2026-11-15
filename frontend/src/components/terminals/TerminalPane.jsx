import React, { useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import { CliLogo } from './CliLogo';
import { renderTerminalText } from './terminalModel';

const URL_SPLIT_REGEX = /(https?:\/\/[^\s<>"'`)\]]+)/g;

// Superficie fija de terminal: negra y sin la estetica de la aplicacion, en cualquier tema.
const TERMINAL_BG = '#000000';
const TERMINAL_FG = '#d4d4d4';
const TERMINAL_DIM = '#6b7280';
const TERMINAL_BORDER = '#1e1f24';

function splitUrlTrailingPunctuation(rawUrl) {
  let clean = rawUrl;
  let trailing = '';
  while (clean.length > 0 && /[.,;:!?]/.test(clean[clean.length - 1])) {
    trailing = clean[clean.length - 1] + trailing;
    clean = clean.slice(0, -1);
  }
  return { clean, trailing };
}

/**
 * Salida del terminal como texto: las URLs se vuelven botones que piden confirmacion antes
 * de abrirse. Todo lo demas se escribe como nodo de texto (React lo escapa), nunca como HTML.
 */
export function renderOutputWithSafeLinks(text, onRequestOpenUrl) {
  const cleaned = renderTerminalText(text);
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
              color: '#6aa9ff',
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

/** Traduce un KeyboardEvent a los bytes que espera un PTY, o null si no aplica. */
export function keyEventToTerminalInput(e) {
  const key = e.key;
  if (!key) return null;
  if (e.ctrlKey && !e.altKey && !e.metaKey) {
    if (key.length === 1 && /[a-zA-Z]/.test(key) && !e.shiftKey) {
      return String.fromCharCode(key.toUpperCase().charCodeAt(0) - 64);
    }
    return null;
  }
  if (e.metaKey) return null;
  switch (key) {
    case 'Enter': return '\r';
    case 'Backspace': return '\x7f';
    case 'Tab': return e.shiftKey ? '\x1b[Z' : '\t';
    case 'Escape': return '\x1b';
    case 'ArrowUp': return '\x1b[A';
    case 'ArrowDown': return '\x1b[B';
    case 'ArrowRight': return '\x1b[C';
    case 'ArrowLeft': return '\x1b[D';
    case 'Home': return '\x1b[H';
    case 'End': return '\x1b[F';
    case 'Delete': return '\x1b[3~';
    case 'PageUp': return '\x1b[5~';
    case 'PageDown': return '\x1b[6~';
    default:
      return key.length === 1 && !e.altKey ? key : null;
  }
}

/**
 * Hueco vacio de la cuadricula: solo la superficie negra. `showHint` (unico caso: no hay
 * NINGUNA terminal) deja una invitacion minima y discreta.
 */
export function EmptyTerminalSlot({ index, showHint = false }) {
  return (
    <section
      data-testid={`terminal-pane-empty-${index}`}
      aria-label="Panel vacío"
      style={{
        position: 'relative',
        minHeight: 0,
        backgroundColor: TERMINAL_BG,
        border: `1px solid ${TERMINAL_BORDER}`,
        borderRadius: 'var(--radius-md)',
        overflow: 'hidden'
      }}
    >
      {showHint && (
        <span
          data-testid="matrix-empty-hint"
          style={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            transform: 'translate(-50%, -50%)',
            fontFamily: 'var(--font-mono)',
            fontSize: '11px',
            color: TERMINAL_DIM,
            opacity: 0.7,
            textAlign: 'center',
            padding: '0 16px'
          }}
        >
          Abre una terminal desde la pestaña lateral →
        </span>
      )}
    </section>
  );
}

/**
 * Un panel = una terminal. Logotipo discreto del CLI y la carpeta de trabajo arriba; el resto
 * es la salida del proceso y el teclado va directo al PTY.
 */
export function TerminalPane({
  pane,
  label,
  cli,
  isFocused,
  focusToken,
  onFocus,
  onInput,
  onRequestOpenUrl,
  registerTerminalRef
}) {
  const preRef = useRef(null);
  const stickRef = useRef(true);
  const paneId = pane.paneId;

  const setRefs = (el) => {
    preRef.current = el;
    registerTerminalRef?.(paneId, el);
  };

  const rendered = useMemo(
    () => renderOutputWithSafeLinks(pane.output, onRequestOpenUrl),
    [pane.output, onRequestOpenUrl]
  );

  // Pegado al final salvo que la persona haya subido a leer el historial.
  useLayoutEffect(() => {
    const el = preRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [pane.output, pane.exitCode]);

  useEffect(() => {
    if (focusToken && isFocused) preRef.current?.focus();
    // Solo reacciona a una peticion nueva de foco.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusToken]);

  const handleScroll = (e) => {
    const el = e.currentTarget;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
  };

  const handleKeyDown = (e) => {
    if (!pane.alive) return;
    if (e.ctrlKey && !e.altKey && !e.metaKey && e.key.toLowerCase() === 'c') {
      const sel = typeof window !== 'undefined' ? window.getSelection?.()?.toString() : '';
      if (sel) return; // copiar el texto seleccionado
    }
    if (e.ctrlKey && !e.altKey && !e.metaKey && e.key.toLowerCase() === 'v') return; // deja pasar el pegado
    const payload = keyEventToTerminalInput(e);
    if (payload !== null) {
      e.preventDefault();
      onInput(paneId, payload);
    }
  };

  const handlePaste = (e) => {
    if (!pane.alive) return;
    const text = e.clipboardData?.getData?.('text');
    e.preventDefault();
    if (text) onInput(paneId, text.replace(/\r?\n/g, '\r'));
  };

  const exited = !pane.alive && pane.exitCode !== null && pane.exitCode !== undefined;
  const logoName = cli?.display_name || pane.cliProfile;

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
      </div>

      <pre
        ref={setRefs}
        data-testid={`pane-output-${paneId}`}
        tabIndex={0}
        aria-label={`Terminal: ${label}`}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        onScroll={handleScroll}
        style={{
          flex: 1,
          minHeight: 0,
          margin: 0,
          padding: '4px 10px 8px',
          backgroundColor: TERMINAL_BG,
          color: TERMINAL_FG,
          fontFamily: 'var(--font-mono)',
          fontSize: '12px',
          lineHeight: 1.4,
          overflowY: 'auto',
          overflowX: 'hidden',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          outline: 'none',
          userSelect: 'text',
          caretColor: TERMINAL_FG
        }}
      >
        {rendered}
        {exited && (
          <span data-testid={`pane-exit-${paneId}`} style={{ display: 'block', color: TERMINAL_DIM }}>
            {`[proceso terminado, código ${pane.exitCode}]`}
          </span>
        )}
      </pre>
    </section>
  );
}
