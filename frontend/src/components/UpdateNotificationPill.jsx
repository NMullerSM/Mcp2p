import React from 'react';

export function UpdateNotificationPill({ release, onOpenModal, onDismiss, progress }) {
  const isDownloading = progress?.status === 'downloading';
  const isReady = progress?.status === 'ready_to_restart';

  const label = isReady
    ? 'Actualización lista'
    : isDownloading
    ? `Descargando... ${progress.percent}%`
    : 'Nueva versión disponible';

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        position: 'fixed',
        bottom: '16px',
        left: '16px',
        zIndex: 40,
        display: 'flex',
        alignItems: 'center',
        gap: '10px',
        padding: '10px 14px',
        borderRadius: 'var(--radius-lg)',
        border: `1px solid ${isReady ? 'var(--accent-success)' : 'var(--border-subtle)'}`,
        background: 'var(--bg-card)',
        boxShadow: 'var(--shadow-lg)',
        fontFamily: 'var(--font-ui)',
        fontSize: '13px',
      }}
    >
      <span style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
        {label} <code style={{ color: 'var(--accent-primary)' }}>{release.tag_name}</code>
      </span>
      <button
        type="button"
        onClick={onOpenModal}
        style={{
          padding: '4px 10px',
          borderRadius: 'var(--radius-md)',
          border: 'none',
          background: isReady ? 'var(--accent-success)' : 'var(--accent-primary)',
          color: '#fff',
          fontWeight: 700,
          fontSize: '12px',
          cursor: 'pointer',
        }}
      >
        {isReady ? 'Reiniciar' : isDownloading ? 'Ver progreso' : 'Actualizar'}
      </button>
      {!isDownloading && !isReady && (
        <button
          type="button"
          onClick={onDismiss}
          aria-label="Descartar"
          style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}
        >
          ✕
        </button>
      )}
    </div>
  );
}
