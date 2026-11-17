import React from 'react';
import { ShieldAlert, ShieldCheck } from 'lucide-react';
import { storageLabel } from './relayModel';
import { chip, mutedText, sectionTitle, toneBox } from './relayStyles';

const COUNT_LABELS = [
  ['queued', 'en cola'],
  ['delivered', 'entregados'],
  ['acked', 'confirmados'],
  ['parked', 'retenidos'],
  ['discarded', 'descartados'],
  ['expired', 'caducados']
];

/**
 * Estado de la auditoría encadenada del relevo + interruptor de la parada global.
 * Bajar la autonomía (activar la parada) no pide confirmación; reanudar abre un diálogo nativo en el backend.
 */
export function RelayStatus({ audit, auditError, loading, globalPaused, busy = false, feedback = null, onSetGlobalPause }) {
  const chain = audit?.chain;
  const counts = audit?.messages || {};
  const device = audit?.device;
  return (
    <section data-testid="relay-status" aria-label="Estado del relevo" style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
      <h4 style={sectionTitle}>Relevo entre agentes</h4>

      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          padding: '8px 10px',
          borderRadius: 'var(--radius-md)',
          border: `1px solid ${globalPaused ? 'rgba(245, 158, 11, 0.45)' : 'var(--border-subtle)'}`,
          backgroundColor: globalPaused ? 'rgba(245, 158, 11, 0.08)' : 'var(--bg-card)'
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
          <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)' }}>Parada global del relevo</span>
          <span data-testid="relay-pause-description" style={mutedText}>
            {globalPaused
              ? 'Ningún agente recibe ni envía mensajes. Los mensajes quedan guardados y se entregarán al reanudar.'
              : 'Detiene todo el relevo entre agentes al instante. No cierra ninguna terminal.'}
          </span>
        </div>
        <button
          type="button"
          role="switch"
          data-testid="relay-global-pause-switch"
          aria-checked={globalPaused ? 'true' : 'false'}
          aria-label="Parada global del relevo"
          disabled={busy || !onSetGlobalPause}
          onClick={() => onSetGlobalPause?.(!globalPaused)}
          style={{
            marginLeft: 'auto',
            flexShrink: 0,
            width: '38px',
            height: '20px',
            borderRadius: '10px',
            border: '1px solid var(--border-subtle)',
            padding: 0,
            position: 'relative',
            backgroundColor: globalPaused ? 'var(--accent-warning)' : 'var(--bg-subtle)',
            cursor: busy ? 'wait' : 'pointer'
          }}
        >
          <span
            style={{
              position: 'absolute',
              top: '2px',
              left: globalPaused ? '20px' : '2px',
              width: '14px',
              height: '14px',
              borderRadius: '50%',
              backgroundColor: '#ffffff',
              transition: 'left 0.15s ease'
            }}
          />
        </button>
      </div>

      {feedback && (
        <div data-testid="relay-status-feedback" role="status" style={toneBox(feedback.tone)}>
          {feedback.text}
        </div>
      )}

      {auditError && (
        <div data-testid="relay-audit-error" role="alert" style={toneBox('error')}>
          {auditError}
        </div>
      )}

      {loading && !audit && !auditError && (
        <p data-testid="relay-audit-loading" style={{ ...mutedText, margin: 0 }}>Comprobando la auditoría…</p>
      )}

      {chain && (
        <div
          data-testid="relay-audit-chain"
          data-chain-ok={chain.ok ? 'true' : 'false'}
          role={chain.ok ? 'status' : 'alert'}
          style={{ ...toneBox(chain.ok ? 'ok' : 'error'), display: 'flex', gap: '7px', alignItems: 'flex-start' }}
        >
          {chain.ok ? <ShieldCheck size={14} style={{ flexShrink: 0 }} /> : <ShieldAlert size={14} style={{ flexShrink: 0 }} />}
          <span>
            {chain.ok
              ? `Auditoría íntegra: ${chain.rows ?? 0} registros encadenados sin alteraciones.`
              : `La cadena de auditoría está rota en el registro n.º ${chain.first_bad_seq ?? '?'} (${chain.reason || 'motivo desconocido'}). ` +
                'Alguien modificó o borró registros. Conserve una copia de la base de datos y no confíe en el historial anterior a ese punto.'}
          </span>
        </div>
      )}

      {audit && (
        <div data-testid="relay-audit-counts" style={{ display: 'flex', gap: '5px', flexWrap: 'wrap' }}>
          {COUNT_LABELS.filter(([key]) => counts[key] !== undefined).map(([key, label]) => (
            <span key={key} data-testid={`relay-count-${key}`} style={chip}>{counts[key]} {label}</span>
          ))}
          {audit.max_chain_depth !== undefined && (
            <span style={chip} title="Respuestas automáticas permitidas antes de esperar al humano">
              cadena máx. {audit.max_chain_depth}
            </span>
          )}
          {audit.retention_days !== undefined && (
            <span style={chip} title="El texto se borra tras este plazo; las huellas se conservan">
              texto {audit.retention_days} días
            </span>
          )}
        </div>
      )}

      {device && (
        <p data-testid="relay-device" style={{ ...mutedText, margin: 0 }}>
          Clave de firma del dispositivo: {storageLabel(device.storage)}
          {device.has_certificate ? ' (con certificado del humano)' : ' (sin certificado: la clave humana está bloqueada)'}.
          {device.warning ? ` Aviso: ${device.warning}` : ''}
        </p>
      )}
    </section>
  );
}
