import React from 'react';
import { BUBBLE_COLORS, bubbleSide, tickInfo } from './chatModel';

const TICK_TONE_COLOR = {
  muted: 'var(--text-muted)',
  ok: 'var(--accent-success)',
  warn: 'var(--accent-warning)',
  danger: 'var(--accent-danger)'
};

function pad(n) {
  return String(n).padStart(2, '0');
}

/** Hora local HH:MM; vacio si la marca de tiempo no es valida. */
export function formatTime(tsMs) {
  const date = new Date(Number(tsMs));
  if (!Number.isFinite(date.getTime())) return '';
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function isoOrUndefined(tsMs) {
  const date = new Date(Number(tsMs));
  return Number.isFinite(date.getTime()) ? date.toISOString() : undefined;
}

// Los mensajes del humano usan el color de marca; los demas, el color asignado a su emisor.
function bubbleColors(entry) {
  if (entry?.sender?.kind === 'human') {
    return { border: 'var(--accent-primary)', bg: 'rgba(var(--accent-primary-rgb), 0.16)' };
  }
  return BUBBLE_COLORS[entry?.sender?.color] || { border: 'var(--border-subtle)', bg: 'var(--bg-subtle)' };
}

// Un origen distinto de este equipo se marca para que no se confunda con un agente local.
function remoteOrigin(origin) {
  if (!origin) return null;
  if (typeof origin === 'object' && origin.local === true) return null;
  const label = typeof origin === 'string' ? origin : String(origin.label || '');
  if (!label || label === 'local') return null;
  return label;
}

/**
 * Burbuja de un mensaje. El texto va como nodo de texto (nunca HTML) y el estado de entrega
 * se muestra con simbolo, etiqueta visible y aria-label: el color nunca es el unico canal.
 */
export function ChatBubble({ entry, continuation = false }) {
  const side = bubbleSide(entry);
  const isHuman = side === 'right';
  const colors = bubbleColors(entry);
  const sender = entry.sender || {};
  const recipients = (Array.isArray(entry.recipients) ? entry.recipients : [])
    .map((r) => r?.label || r?.principal || '')
    .filter(Boolean);
  const tick = tickInfo(entry.tick);
  const tickLabel = entry.tick_label || '';
  const origin = remoteOrigin(entry.origin);
  const time = formatTime(entry.ts_ms);

  return (
    <div
      data-testid="chat-bubble"
      data-side={side}
      data-continuation={continuation ? 'true' : 'false'}
      style={{
        display: 'flex',
        justifyContent: isHuman ? 'flex-end' : 'flex-start',
        marginTop: continuation ? '2px' : '10px'
      }}
    >
      <div style={{
        maxWidth: '85%',
        minWidth: 0,
        display: 'flex',
        flexDirection: 'column',
        alignItems: isHuman ? 'flex-end' : 'flex-start'
      }}>
        {!continuation && (
          <div style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: '4px',
            fontSize: '11px',
            color: 'var(--text-secondary)',
            marginBottom: '2px',
            justifyContent: isHuman ? 'flex-end' : 'flex-start'
          }}>
            <strong style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{sender.label}</strong>
            {sender.role_label && <span>({sender.role_label})</span>}
            {recipients.length > 0 && <span>→ {recipients.join(', ')}</span>}
          </div>
        )}

        <div style={{
          padding: '6px 10px',
          borderRadius: 'var(--radius-md)',
          border: `1px solid ${colors.border}`,
          backgroundColor: colors.bg,
          color: 'var(--text-primary)',
          fontSize: '13px',
          lineHeight: 1.4,
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
          minWidth: 0,
          maxWidth: '100%'
        }}>
          {String(entry.text ?? '')}
        </div>

        <div style={{
          display: 'flex',
          flexWrap: 'wrap',
          alignItems: 'center',
          gap: '8px',
          fontSize: '10.5px',
          color: 'var(--text-muted)',
          marginTop: '2px',
          justifyContent: isHuman ? 'flex-end' : 'flex-start'
        }}>
          {time && <time dateTime={isoOrUndefined(entry.ts_ms)}>{time}</time>}
          {entry.tick && (
            <span
              data-tone={tick.tone}
              aria-label={tickLabel || entry.tick}
              title={tickLabel || entry.tick}
              style={{ color: TICK_TONE_COLOR[tick.tone] || TICK_TONE_COLOR.muted, fontWeight: 600 }}
            >
              {tick.symbol} {tickLabel}
            </span>
          )}
          {origin && <span>Desde {origin}</span>}
        </div>
      </div>
    </div>
  );
}
