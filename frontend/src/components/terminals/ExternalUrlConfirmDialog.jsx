import React from 'react';
import { ExternalLink, X } from 'lucide-react';

export function ExternalUrlConfirmDialog({ url, onCancel, onConfirm }) {
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
