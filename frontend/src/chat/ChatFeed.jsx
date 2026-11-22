import React, { useLayoutEffect, useMemo, useRef } from 'react';
import { ChatBubble } from './ChatBubble';
import { groupConsecutive } from './chatModel';

// Distancia (px) al final dentro de la que se considera que el usuario esta "abajo" y se
// sigue el chat al llegar mensajes. Si ha subido a leer, no se le mueve la vista.
const STICK_TO_BOTTOM_PX = 32;

const EMPTY_TEXT = 'Todavía no hay mensajes entre agentes en este proyecto';

/**
 * Lista de mensajes del chat. Es una region `log` en vivo; muestra el error con reintento y,
 * aparte, las fuentes que no respondieron.
 */
export function ChatFeed({ entries = [], sources = [], error = null, loading = false, onRetry }) {
  const logRef = useRef(null);
  const stickRef = useRef(true);

  const rows = useMemo(
    () => groupConsecutive(entries).flatMap((group) =>
      group.entries.map((entry, index) => ({ entry, continuation: index > 0 }))
    ),
    [entries]
  );
  const failedSources = (Array.isArray(sources) ? sources : []).filter((s) => s && s.ok === false);

  useLayoutEffect(() => {
    const el = logRef.current;
    if (el && stickRef.current) el.scrollTop = el.scrollHeight;
  }, [entries]);

  const onScroll = () => {
    const el = logRef.current;
    if (!el) return;
    stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_TO_BOTTOM_PX;
  };

  let emptyNotice = null;
  if (rows.length === 0 && !error) {
    emptyNotice = (
      <p style={{ margin: '16px 4px', fontSize: '12px', color: 'var(--text-muted)', lineHeight: 1.4 }}>
        {loading ? 'Cargando mensajes…' : EMPTY_TEXT}
      </p>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, minWidth: 0 }}>
      {error && (
        <div
          role="alert"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: '8px',
            margin: '8px 12px 0',
            padding: '6px 9px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid rgba(239, 68, 68, 0.35)',
            backgroundColor: 'rgba(239, 68, 68, 0.1)',
            color: 'var(--accent-danger)',
            fontSize: '11.5px',
            lineHeight: 1.4
          }}
        >
          <span style={{ flex: 1, minWidth: 0, overflowWrap: 'anywhere' }}>No se pudo leer el chat: {error}</span>
          {onRetry && (
            <button
              type="button"
              onClick={() => onRetry()}
              style={{
                padding: '3px 9px',
                fontSize: '11.5px',
                fontWeight: 600,
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--accent-danger)',
                backgroundColor: 'transparent',
                color: 'var(--accent-danger)',
                cursor: 'pointer'
              }}
            >
              Reintentar
            </button>
          )}
        </div>
      )}

      <div
        ref={logRef}
        role="log"
        aria-live="polite"
        aria-label="Mensajes del chat"
        onScroll={onScroll}
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: 'auto',
          overflowX: 'hidden',
          padding: '4px 12px 8px',
          minWidth: 0
        }}
      >
        {emptyNotice}
        {rows.map(({ entry, continuation }) => (
          <ChatBubble key={entry.id} entry={entry} continuation={continuation} />
        ))}
      </div>

      {failedSources.map((source) => (
        <p
          key={source.id}
          data-testid="chat-source-issue"
          style={{ margin: '0 12px 6px', fontSize: '10.5px', color: 'var(--text-muted)', lineHeight: 1.4 }}
        >
          Fuente {source.id} sin respuesta: {source.reason || 'motivo desconocido'}
        </p>
      ))}
    </div>
  );
}
