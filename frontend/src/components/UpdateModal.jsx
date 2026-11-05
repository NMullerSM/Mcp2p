import React, { useState, useEffect, useRef } from 'react';

export function UpdateModal({ release, currentVersion, api, onClose, onIgnoreVersion, progress: externalProgress }) {
  const [internalProgress, setInternalProgress] = useState({ status: 'idle', percent: 0, downloaded_mb: 0, total_mb: 0 });
  const [isApplyingRestart, setIsApplyingRestart] = useState(false);
  const [actionError, setActionError] = useState(null);
  const pollRef = useRef(null);

  const activeProgress = externalProgress || internalProgress;
  const isDownloading = activeProgress.status === 'downloading';
  const isReady = activeProgress.status === 'ready_to_restart';
  const hasError = activeProgress.status === 'error' || Boolean(actionError);

  const isWindows = typeof navigator !== 'undefined' && /win/i.test(navigator.userAgent);
  const exeAsset = release.assets?.find((a) => a.name.toLowerCase().endsWith('.exe'));
  const debAsset = release.assets?.find((a) => a.name.toLowerCase().endsWith('.deb'));
  const tarAsset = release.assets?.find((a) => a.name.toLowerCase().includes('linux') && a.name.toLowerCase().endsWith('.tar.gz'));
  const primaryAsset = isWindows ? exeAsset : (debAsset || tarAsset || exeAsset);

  useEffect(() => {
    const checkStatus = async () => {
      try {
        const prog = await api.get_update_progress();
        setInternalProgress(prog);
      } catch (err) {
        console.warn('Error al consultar progreso de actualización:', err);
      }
    };
    checkStatus();
    if (activeProgress.status === 'downloading') {
      pollRef.current = setInterval(checkStatus, 400);
    } else if (pollRef.current) {
      clearInterval(pollRef.current);
    }
    return () => pollRef.current && clearInterval(pollRef.current);
  }, [activeProgress.status, api]);

  const handleStartDownload = async () => {
    setActionError(null);
    if (!primaryAsset) {
      window.open(release.html_url, '_blank');
      return;
    }
    try {
      setInternalProgress((prev) => ({ ...prev, status: 'downloading', percent: 0 }));
      const res = await api.start_auto_update(primaryAsset.browser_download_url, release.tag_name);
      if (!res.success && res.error) {
        setActionError(res.error);
        setInternalProgress((prev) => ({ ...prev, status: 'error', error: res.error }));
      }
    } catch (err) {
      setActionError(err?.message || 'Error al iniciar la descarga');
    }
  };

  const handleApplyRestart = async () => {
    setActionError(null);
    setIsApplyingRestart(true);
    try {
      const res = await api.apply_update_and_restart();
      if (!res.success && res.error) {
        setActionError(res.error);
        setIsApplyingRestart(false);
      }
    } catch (err) {
      setActionError(err?.message || 'Error al reiniciar');
      setIsApplyingRestart(false);
    }
  };

  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.6)' }}>
      <div style={{ width: '100%', maxWidth: '480px', background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-lg)', padding: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '12px' }}>
          <h2 style={{ color: 'var(--text-primary)', margin: 0 }}>Actualización disponible</h2>
          <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}>✕</button>
        </div>
        <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
          {currentVersion} → <strong style={{ color: 'var(--accent-primary)' }}>{release.tag_name}</strong>
        </p>

        {isDownloading && (
          <div style={{ margin: '12px 0' }}>
            <div style={{ height: '8px', borderRadius: '4px', background: 'var(--bg-subtle)', overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${Math.max(5, activeProgress.percent)}%`, background: 'var(--accent-primary)' }} />
            </div>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{activeProgress.percent}% — {activeProgress.downloaded_mb} MB / {activeProgress.total_mb} MB</p>
          </div>
        )}

        {isReady && (
          <button onClick={handleApplyRestart} disabled={isApplyingRestart} style={{ width: '100%', padding: '10px', borderRadius: 'var(--radius-md)', border: 'none', background: 'var(--accent-success)', color: '#fff', fontWeight: 700, cursor: 'pointer' }}>
            {isApplyingRestart ? 'Iniciando instalador...' : 'Reiniciar para actualizar'}
          </button>
        )}

        {hasError && (
          <p style={{ color: 'var(--accent-danger)', fontSize: '12px' }}>{actionError || activeProgress.error}</p>
        )}

        <pre style={{ whiteSpace: 'pre-wrap', fontSize: '12px', color: 'var(--text-secondary)', background: 'var(--bg-card)', padding: '10px', borderRadius: 'var(--radius-md)', maxHeight: '160px', overflowY: 'auto' }}>
          {release.body || 'Mejoras y correcciones generales.'}
        </pre>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '12px' }}>
          {onIgnoreVersion && !isDownloading && !isReady && (
            <button onClick={() => onIgnoreVersion(release.tag_name)} style={{ marginRight: 'auto', background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: '12px', cursor: 'pointer', textDecoration: 'underline' }}>
              No avisar de esta versión
            </button>
          )}
          <button onClick={onClose} style={{ padding: '8px 14px', borderRadius: 'var(--radius-md)', border: '1px solid var(--border-subtle)', background: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }}>
            Cerrar
          </button>
          {!isDownloading && !isReady && (
            <button onClick={handleStartDownload} style={{ padding: '8px 14px', borderRadius: 'var(--radius-md)', border: 'none', background: 'var(--accent-primary)', color: '#fff', fontWeight: 700, cursor: 'pointer' }}>
              Actualizar
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
