import React from 'react';

/**
 * Banners superiores de estado en la matriz: aviso de diálogo nativo pendiente
 * y alerta de Loop Breaker activado (EGM-87).
 */
export function MatrixBanners({
  pendingNativeConfirmations = 0,
  trippedCases = [],
  resettingBreaker = false,
  canResetBreaker = false,
  onResetLoopBreaker
}) {
  if (pendingNativeConfirmations <= 0 && trippedCases.length === 0) {
    return null;
  }

  return (
    <>
      {pendingNativeConfirmations > 0 && (
        <div
          role="status"
          data-testid="native-dialog-pending-banner"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            marginBottom: '8px',
            padding: '7px 10px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid rgba(59, 130, 246, 0.45)',
            backgroundColor: 'rgba(59, 130, 246, 0.1)',
            color: 'var(--text-primary)',
            fontSize: '11.5px',
            flexShrink: 0
          }}
        >
          <span>Confirme la acción en el diálogo del sistema…</span>
        </div>
      )}

      {/* El Auto-Relay vive ahora en el slider lateral (seccion 'Auto-Relay'); aqui solo queda
          el aviso de Loop Breaker cuando se dispara (EGM-87). */}
      {trippedCases.length > 0 && (
        <div
          role="alert"
          data-testid="loop-breaker-banner"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            flexWrap: 'wrap',
            gap: '8px',
            marginBottom: '8px',
            padding: '7px 10px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid rgba(239, 68, 68, 0.45)',
            backgroundColor: 'rgba(239, 68, 68, 0.1)',
            color: 'var(--text-primary)',
            fontSize: '11.5px',
            flexShrink: 0
          }}
        >
          <span>
            Loop Breaker activado en: <strong>{trippedCases.map((c) => c.label).join(', ')}</strong>
          </span>
          <button
            type="button"
            data-testid="reset-loop-breaker-btn"
            disabled={resettingBreaker || !canResetBreaker}
            onClick={() => onResetLoopBreaker?.()}
            style={{
              padding: '4px 10px',
              fontSize: '11.5px',
              fontWeight: 700,
              borderRadius: 'var(--radius-md)',
              border: '1px solid rgba(239, 68, 68, 0.5)',
              backgroundColor: 'rgba(239, 68, 68, 0.18)',
              color: '#fca5a5',
              cursor: resettingBreaker ? 'wait' : 'pointer'
            }}
          >
            Reiniciar Loop Breaker
          </button>
        </div>
      )}
    </>
  );
}
