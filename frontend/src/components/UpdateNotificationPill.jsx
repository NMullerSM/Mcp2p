import React from 'react';

export function UpdateNotificationPill({ release, onOpenModal, onDismiss, progress }) {
  const status = progress?.status;
  const isDownloading = status === 'downloading';
  const isVerifying = status === 'verifying';
  const isInstalling = status === 'installing';
  const isReady = status === 'ready_to_restart';
  const isError = status === 'error';
  const isBusy = isDownloading || isVerifying || isInstalling;

  const label = isReady
    ? 'Actualización lista'
    : isDownloading
    ? `Descargando... ${progress.percent}%`
    : isVerifying
    ? 'Verificando actualización...'
    : isInstalling
    ? 'Instalando actualización...'
    : isError
    ? 'Falló la actualización'
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
        border: `1px solid ${isReady ? 'var(--accent-success)' : isError ? 'var(--accent-danger)' : 'var(--border-subtle)'}`,
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
        {isReady ? 'Reiniciar' : isBusy ? 'Ver progreso' : isError ? 'Ver detalle' : 'Actualizar'}
      </button>
      {!isBusy && !isReady && (
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
