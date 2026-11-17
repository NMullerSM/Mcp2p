import React, { useState, useEffect, useRef, useCallback } from 'react';

const IDLE = { status: 'idle', percent: 0, downloaded_mb: 0, total_mb: 0 };
const POLLING_STATES = ['downloading', 'verifying'];

/**
 * Elige el asset de la release que corresponde a CÓMO está instalada esta copia
 * (`installMode.kind`, ver get_install_info en src/app/auto_updater.py). Si todavía no se
 * conoce, recurre a una heurística por user-agent. Devuelve `undefined` si la release no
 * trae un asset apto (la UI lo muestra: nunca falla en silencio).
 */
export function pickUpdateAsset(assets, installMode, isWindowsUserAgent = false) {
  const list = Array.isArray(assets) ? assets : [];
  const name = (a) => String(a?.name || '').toLowerCase();
  const exe = list.find((a) => name(a).endsWith('.exe'));
  const winZip = list.find((a) => name(a).includes('windows') && name(a).endsWith('.zip'));
  const deb = list.find((a) => name(a).endsWith('.deb'));
  const tar = list.find((a) => name(a).includes('linux') && name(a).endsWith('.tar.gz'));

  switch (installMode?.kind) {
    case 'windows_installer': return exe;
    case 'windows_portable': return winZip;
    case 'linux_deb': return deb || tar;
    case 'linux_portable': return tar || deb;
    default: break;
  }
  if (installMode?.platform === 'win32') return exe || winZip;
  if (installMode?.platform?.startsWith('linux')) {
    return installMode.mode === 'system' ? (deb || tar) : (tar || deb);
  }
  return isWindowsUserAgent ? exe : (deb || tar || exe);
}

const KIND_NOTES = {
  windows_installer: 'Se descargará el instalador, se verificará (SHA256) y se instalará solo; la aplicación se cerrará y volverá a abrirse.',
  windows_portable: 'Se descargará el paquete portable, se verificará (SHA256) y se reemplazarán los archivos de esta carpeta; tu base de datos y proyectos no se tocan.',
  linux_deb: 'Se descargará el paquete .deb y se pedirá autorización del sistema para instalarlo.',
  linux_portable: 'Se descargará el paquete portable, se verificará (SHA256) y se reemplazará esta carpeta sin tocar tus datos.',
};

const btnBase = { padding: '8px 14px', borderRadius: 'var(--radius-md)', cursor: 'pointer' };

export function UpdateModal({ release, currentVersion, api, installMode: installModeProp, onClose, onIgnoreVersion, progress: externalProgress, onProgressChange }) {
  const [progress, setProgress] = useState(externalProgress || IDLE);
  const [installModeLocal, setInstallModeLocal] = useState(null);
  const [isApplying, setIsApplying] = useState(false);
  const [actionError, setActionError] = useState(null);
  const [showLog, setShowLog] = useState(false);
  const [logText, setLogText] = useState(null);
  const pollRef = useRef(null);

  const installMode = installModeProp || installModeLocal;
  const status = progress.status;
  const isBusy = POLLING_STATES.includes(status);
  const isReady = status === 'ready_to_restart';
  const isInstalling = status === 'installing';
  const errorText = actionError || (status === 'error' ? (progress.error || 'La actualización falló por una causa desconocida.') : null);

  const isWindowsUA = typeof navigator !== 'undefined' && /win/i.test(navigator.userAgent);
  const primaryAsset = pickUpdateAsset(release.assets, installMode, isWindowsUA);

  const publish = useCallback((next) => {
    setProgress(next);
    if (onProgressChange) onProgressChange(next);
  }, [onProgressChange]);

  // El estado que mantiene la app (pastilla) puede cambiar desde fuera: se refleja aquí.
  useEffect(() => {
    if (externalProgress) setProgress(externalProgress);
  }, [externalProgress]);

  // Si la app todavía no entregó el modo de instalación, se pide aquí (decide el asset).
  useEffect(() => {
    if (installModeProp || !api || typeof api.get_install_mode !== 'function') return undefined;
    let alive = true;
    (async () => {
      try {
        const res = await api.get_install_mode();
        if (alive && res?.success) setInstallModeLocal(res);
      } catch { /* sin modo conocido: se usa la heurística por user-agent */ }
    })();
    return () => { alive = false; };
  }, [api, installModeProp]);

  // Sondeo propio mientras se descarga/verifica, y una consulta inicial al abrir.
  useEffect(() => {
    if (!api || typeof api.get_update_progress !== 'function') return undefined;
    let alive = true;
    const poll = async () => {
      try {
        const prog = await api.get_update_progress();
        if (alive && prog) publish(prog);
      } catch (err) {
        console.warn('Error al consultar progreso de actualización:', err);
      }
    };
    if (status === 'idle') poll();
    if (POLLING_STATES.includes(status)) {
      pollRef.current = setInterval(poll, 400);
    }
    return () => {
      alive = false;
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, api]);

  const loadLog = useCallback(async () => {
    try {
      const res = await api.get_update_log(200);
      setLogText(res?.success ? (res.text || '(el registro está vacío)') : (res?.error || 'No se pudo leer el registro.'));
    } catch (err) {
      setLogText(`No se pudo leer el registro: ${err?.message || err}`);
    }
  }, [api]);

  useEffect(() => {
    if (showLog) loadLog();
  }, [showLog, loadLog, status]);

  const handleStartDownload = async () => {
    setActionError(null);
    if (!primaryAsset) {
      setActionError('Esta versión no incluye un instalador para tu sistema. Usa "Abrir página de descarga" para bajarlo manualmente.');
      return;
    }
    publish({ ...IDLE, status: 'downloading', version: release.tag_name });
    try {
      const res = await api.start_auto_update(primaryAsset.browser_download_url, release.tag_name);
      if (!res || !res.success) {
        const msg = res?.error || 'El programa rechazó iniciar la descarga.';
        setActionError(msg);
        publish({ ...IDLE, status: 'error', error: msg });
      }
    } catch (err) {
      const msg = err?.message || 'Error al iniciar la descarga';
      setActionError(msg);
      publish({ ...IDLE, status: 'error', error: msg });
    }
  };

  const handleApplyRestart = async () => {
    setActionError(null);
    setIsApplying(true);
    try {
      const res = await api.apply_update_and_restart();
      if (!res || !res.success) {
        setActionError(res?.error || 'No se pudo iniciar la instalación.');
        setIsApplying(false);
      } else {
        publish({ ...progress, status: 'installing' });
      }
    } catch (err) {
      // Si el proceso ya se está cerrando, la ventana puede perder el canal: no es un error del usuario.
      setActionError(err?.message || 'Error al reiniciar');
      setIsApplying(false);
    }
  };

  const handleOpenReleasePage = async () => {
    const url = release.html_url;
    try {
      if (api && typeof api.open_release_page === 'function') {
        const res = await api.open_release_page(url);
        if (!res?.success) setActionError(res?.error || 'No se pudo abrir el navegador. Visita: ' + url);
      } else {
        window.open(url, '_blank');
      }
    } catch (err) {
      setActionError(err?.message || 'No se pudo abrir el navegador. Visita: ' + url);
    }
  };

  const percent = Math.max(0, Math.min(100, progress.percent || 0));
  const note = KIND_NOTES[installMode?.kind];

  return (
    <div role="dialog" aria-modal="true" aria-label="Actualización disponible" style={{ position: 'fixed', inset: 0, zIndex: 50, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(0,0,0,0.6)' }}>
      <div style={{ width: '100%', maxWidth: '480px', background: 'var(--bg-surface)', border: '1px solid var(--border-subtle)', borderRadius: 'var(--radius-lg)', padding: '20px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '12px' }}>
          <h2 style={{ color: 'var(--text-primary)', margin: 0 }}>Actualización disponible</h2>
          <button onClick={onClose} aria-label="Cerrar" style={{ background: 'none', border: 'none', color: 'var(--text-muted)', cursor: 'pointer' }}>✕</button>
        </div>
        <p style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>
          {currentVersion} → <strong style={{ color: 'var(--accent-primary)' }}>{release.tag_name}</strong>
        </p>

        {status === 'idle' && note && !errorText && (
          <p style={{ color: 'var(--text-muted)', fontSize: '12px' }}>{note}</p>
        )}

        {isBusy && (
          <div role="status" aria-live="polite" style={{ margin: '12px 0' }}>
            <p style={{ fontSize: '13px', color: 'var(--text-primary)', margin: '0 0 6px' }}>
              {status === 'verifying' ? 'Verificando integridad (SHA256)...' : 'Descargando actualización...'}
            </p>
            <div style={{ height: '8px', borderRadius: '4px', background: 'var(--bg-subtle)', overflow: 'hidden' }}>
              <div style={{ height: '100%', width: `${Math.max(5, percent)}%`, background: 'var(--accent-primary)' }} />
            </div>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)' }}>{percent}% — {progress.downloaded_mb} MB / {progress.total_mb} MB</p>
          </div>
        )}

        {isInstalling && (
          <p role="status" aria-live="polite" style={{ color: 'var(--text-primary)', fontSize: '13px' }}>
            Instalando la actualización... MCP2P se cerrará y se volverá a abrir sola. Si no lo hace en un minuto, ábrela desde el menú Inicio.
          </p>
        )}

        {isReady && (
          <div>
            <p style={{ color: 'var(--accent-success)', fontSize: '13px', margin: '0 0 8px' }}>
              Descarga verificada. Al continuar, MCP2P se cerrará, se instalará la nueva versión y se volverá a abrir.
            </p>
            <button onClick={handleApplyRestart} disabled={isApplying} style={{ width: '100%', padding: '10px', borderRadius: 'var(--radius-md)', border: 'none', background: 'var(--accent-success)', color: '#fff', fontWeight: 700, cursor: 'pointer' }}>
              {isApplying ? 'Iniciando instalador...' : 'Reiniciar para actualizar'}
            </button>
          </div>
        )}

        {errorText && (
          <div role="alert" style={{ margin: '8px 0' }}>
            <p style={{ color: 'var(--accent-danger)', fontSize: '12px', margin: '0 0 8px', wordBreak: 'break-word' }}>{errorText}</p>
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
              {primaryAsset && status === 'error' && (
                <button onClick={handleStartDownload} style={{ ...btnBase, border: 'none', background: 'var(--accent-primary)', color: '#fff', fontWeight: 700 }}>Reintentar</button>
              )}
              <button onClick={handleOpenReleasePage} style={{ ...btnBase, border: '1px solid var(--border-subtle)', background: 'none', color: 'var(--text-secondary)' }}>Abrir página de descarga</button>
              <button onClick={() => setShowLog((v) => !v)} style={{ ...btnBase, border: '1px solid var(--border-subtle)', background: 'none', color: 'var(--text-secondary)' }}>
                {showLog ? 'Ocultar registro' : 'Ver registro de actualización'}
              </button>
            </div>
            {showLog && (
              <pre aria-label="Registro de actualización" style={{ whiteSpace: 'pre-wrap', fontSize: '11px', color: 'var(--text-secondary)', background: 'var(--bg-card)', padding: '8px', borderRadius: 'var(--radius-md)', maxHeight: '140px', overflowY: 'auto', marginTop: '8px' }}>
                {logText === null ? 'Cargando...' : logText}
              </pre>
            )}
          </div>
        )}

        <pre style={{ whiteSpace: 'pre-wrap', fontSize: '12px', color: 'var(--text-secondary)', background: 'var(--bg-card)', padding: '10px', borderRadius: 'var(--radius-md)', maxHeight: '160px', overflowY: 'auto' }}>
          {release.body || 'Mejoras y correcciones generales.'}
        </pre>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginTop: '12px' }}>
          {onIgnoreVersion && !isBusy && !isReady && !isInstalling && (
            <button onClick={() => onIgnoreVersion(release.tag_name)} style={{ marginRight: 'auto', background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: '12px', cursor: 'pointer', textDecoration: 'underline' }}>
              No avisar de esta versión
            </button>
          )}
          <button onClick={onClose} style={{ ...btnBase, border: '1px solid var(--border-subtle)', background: 'none', color: 'var(--text-secondary)' }}>
            Cerrar
          </button>
          {status !== 'error' && !isBusy && !isReady && !isInstalling && (
            <button onClick={handleStartDownload} style={{ ...btnBase, border: 'none', background: 'var(--accent-primary)', color: '#fff', fontWeight: 700 }}>
              Actualizar
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
