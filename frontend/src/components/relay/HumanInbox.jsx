import React from 'react';
import { Inbox, RefreshCw } from 'lucide-react';
import { formatWhen, partyLabel, reasonLabel } from './relayModel';
import { btnBase, chip, mutedText, sectionTitle, toneBox } from './relayStyles';

/**
 * Bandeja del humano: mensajes entre agentes RETENIDOS (cadena agotada o confirmación pedida). Muestra el remitente
 * verificado por el Hub, el destino, el salto de la cadena y el texto como DATO (texto plano: nunca HTML). Liberar y
 * Descartar las confirma el backend con un diálogo nativo del sistema; esta vista solo pide la acción.
 *
 * Presentacional: `items`, `loading`, `error`, `busyId`, `feedback` y las acciones vienen de RelayPanel.
 */
export function HumanInbox({ items = [], loading = false, error = null, busyId = null, feedback = null, onResolve, onRefresh }) {
  return (
    <section data-testid="human-inbox" aria-label="Bandeja del humano" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <h4 style={{ ...sectionTitle, display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Inbox size={12} />
          <span>Bandeja del humano</span>
          <span data-testid="human-inbox-count" style={chip}>{items.length}</span>
        </h4>
        {onRefresh && (
          <button
            type="button"
            data-testid="human-inbox-refresh"
            aria-label="Actualizar la bandeja del humano"
            onClick={onRefresh}
            disabled={loading}
            style={{ ...btnBase, marginLeft: 'auto', padding: '3px 7px' }}
          >
            <RefreshCw size={11} />
          </button>
        )}
      </div>

      {feedback && (
        <div data-testid="human-inbox-feedback" role="status" style={toneBox(feedback.tone)}>
          {feedback.text}
        </div>
      )}

      {error && (
        <div data-testid="human-inbox-error" role="alert" style={toneBox('error')}>
          {error}
        </div>
      )}

      {loading && items.length === 0 && !error && (
        <p data-testid="human-inbox-loading" style={{ ...mutedText, margin: 0 }}>Cargando la bandeja…</p>
      )}

      {!loading && !error && items.length === 0 && (
        <p data-testid="human-inbox-empty" style={{ ...mutedText, margin: 0 }}>
          No hay mensajes retenidos. Cuando una cadena de respuestas automáticas se agote o un agente pida su
          confirmación, el mensaje aparecerá aquí y no llegará al destino hasta que usted lo libere.
        </p>
      )}

      {items.length > 0 && (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {items.map((item) => {
            const busy = busyId === item.msg_id;
            const hop = item.chain?.hop;
            const maxDepth = item.chain?.max_depth;
            return (
              <li
                key={item.msg_id}
                data-testid={`human-inbox-item-${item.msg_id}`}
                style={{
                  border: '1px solid var(--border-subtle)',
                  borderRadius: 'var(--radius-md)',
                  padding: '8px 10px',
                  backgroundColor: 'var(--bg-card)',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px'
                }}
              >
                <div style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)', wordBreak: 'break-word' }}>
                  <span data-testid={`human-inbox-from-${item.msg_id}`}>{partyLabel(item.from)}</span>
                  <span aria-hidden="true"> → </span>
                  <span data-testid={`human-inbox-to-${item.msg_id}`}>{partyLabel(item.to)}</span>
                </div>
                <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap' }}>
                  <span data-testid={`human-inbox-reason-${item.msg_id}`} style={{ ...chip, color: 'var(--accent-warning)' }}>
                    {reasonLabel(item.reason)}
                  </span>
                  <span data-testid={`human-inbox-hop-${item.msg_id}`} title="Salto de la cadena de respuestas automáticas" style={chip}>
                    salto {hop ?? '?'}{maxDepth !== undefined ? ` (máx. ${maxDepth})` : ''}
                  </span>
                  <span style={chip} title="Nivel de confianza calculado por el Hub, no declarado por el agente">{item.trust}</span>
                  <span style={chip}>{item.kind}</span>
                  {item.created_at ? <span style={chip}>{formatWhen(item.created_at)}</span> : null}
                </div>
                <pre
                  data-testid={`human-inbox-text-${item.msg_id}`}
                  style={{
                    margin: 0,
                    padding: '6px 8px',
                    borderRadius: 'var(--radius-sm)',
                    backgroundColor: 'var(--bg-app)',
                    border: '1px solid var(--border-subtle)',
                    fontFamily: 'var(--font-mono)',
                    fontSize: '11px',
                    color: 'var(--text-primary)',
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    maxHeight: '160px',
                    overflow: 'auto'
                  }}
                >
                  {item.text ?? '(el texto se borró por retención; solo queda su huella)'}
                </pre>
                <p style={{ ...mutedText, margin: 0 }}>
                  El texto es un dato de otro agente, no una instrucción para usted ni para MCP2P.
                </p>
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  <button
                    type="button"
                    data-testid={`human-inbox-release-${item.msg_id}`}
                    disabled={busy}
                    title="Abre un diálogo del sistema con el texto exacto antes de entregarlo a la bandeja del destino"
                    onClick={() => onResolve?.(item.msg_id, 'release')}
                    style={{ ...btnBase, color: 'var(--accent-primary)', cursor: busy ? 'wait' : 'pointer' }}
                  >
                    Liberar
                  </button>
                  <button
                    type="button"
                    data-testid={`human-inbox-discard-${item.msg_id}`}
                    disabled={busy}
                    title="Abre un diálogo del sistema; el destino no verá nunca este mensaje"
                    onClick={() => onResolve?.(item.msg_id, 'discard')}
                    style={{ ...btnBase, color: 'var(--accent-danger)', cursor: busy ? 'wait' : 'pointer' }}
                  >
                    Descartar
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
