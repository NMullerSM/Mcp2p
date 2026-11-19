import React, { useCallback, useEffect, useRef, useState } from 'react';

function formatSetAt(iso) {
  if (!iso) return '';
  try {
    return new Date(iso).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' });
  } catch {
    return '';
  }
}

/**
 * Hueco `#ajustes-confirmaciones` de «Terminales y relevo» en Ajustes (WP-9):
 * muestra las acciones propias recordadas con botones «Olvidar» y «Olvidar todas».
 */
export function ConfirmationPrefsSection({ api }) {
  const [prefs, setPrefs] = useState([]);
  const [loading, setLoading] = useState(false);
  const [busyAction, setBusyAction] = useState(null);
  const [error, setError] = useState(null);
  const mountedRef = useRef(true);

  const load = useCallback(async () => {
    if (typeof api?.ui_list_confirmation_prefs !== 'function') return;
    setLoading(true);
    setError(null);
    try {
      const res = await api.ui_list_confirmation_prefs();
      if (!mountedRef.current) return;
      if (res?.success === false) {
        setError(res.message || 'No se pudieron cargar las confirmaciones recordadas.');
        return;
      }
      setPrefs(Array.isArray(res?.prefs) ? res.prefs : []);
    } catch (err) {
      if (mountedRef.current) {
        setError(err?.message || 'No se pudieron cargar las confirmaciones recordadas.');
      }
    } finally {
      if (mountedRef.current) setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    mountedRef.current = true;
    void load();
    return () => {
      mountedRef.current = false;
    };
  }, [load]);

  if (typeof api?.ui_list_confirmation_prefs !== 'function') {
    return null;
  }

  const handleForget = async (actionId) => {
    if (typeof api?.ui_forget_confirmation !== 'function' || busyAction) return;
    setBusyAction(actionId);
    setError(null);
    try {
      const res = await api.ui_forget_confirmation(actionId);
      if (!mountedRef.current) return;
      if (res?.success === false) {
        setError(res.message || 'No se pudo olvidar esta confirmación.');
        return;
      }
      setPrefs((prev) => prev.filter((item) => item.action_id !== actionId));
    } catch (err) {
      if (mountedRef.current) {
        setError(err?.message || 'No se pudo olvidar esta confirmación.');
      }
    } finally {
      if (mountedRef.current) setBusyAction(null);
    }
  };

  const handleForgetAll = async () => {
    if (typeof api?.ui_forget_all_confirmations !== 'function' || busyAction) return;
    setBusyAction('__all__');
    setError(null);
    try {
      const res = await api.ui_forget_all_confirmations();
      if (!mountedRef.current) return;
      if (res?.success === false) {
        setError(res.message || 'No se pudieron olvidar las confirmaciones.');
        return;
      }
      setPrefs([]);
    } catch (err) {
      if (mountedRef.current) {
        setError(err?.message || 'No se pudieron olvidar las confirmaciones.');
      }
    } finally {
      if (mountedRef.current) setBusyAction(null);
    }
  };

  return (
    <div
      data-testid="confirmation-prefs-section"
      style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '6px' }}
    >
      {error && (
        <div
          role="alert"
          data-testid="confirmation-prefs-error"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: '8px',
            flexWrap: 'wrap',
            padding: '6px 10px',
            borderRadius: 'var(--radius-sm)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-subtle)',
            fontSize: '11.5px',
            color: 'var(--accent-danger, #ef4444)'
          }}
        >
          <span>{error}</span>
          <button
            type="button"
            onClick={() => void load()}
            style={{
              padding: '3px 8px',
              fontSize: '11px',
              borderRadius: 'var(--radius-sm)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-surface)',
              color: 'var(--text-primary)',
              cursor: 'pointer'
            }}
          >
            Reintentar
          </button>
        </div>
      )}

      {loading && prefs.length === 0 ? (
        <p data-testid="confirmation-prefs-loading" style={{ margin: 0, fontSize: '11.5px', color: 'var(--text-muted)' }}>
          Cargando confirmaciones recordadas…
        </p>
      ) : prefs.length === 0 ? (
        <p data-testid="confirmation-prefs-empty" style={{ margin: 0, fontSize: '11.5px', color: 'var(--text-muted)' }}>
          No hay confirmaciones recordadas.
        </p>
      ) : (
        <>
          <ul
            data-testid="confirmation-prefs-list"
            style={{
              listStyle: 'none',
              margin: 0,
              padding: 0,
              display: 'flex',
              flexDirection: 'column',
              gap: '6px'
            }}
          >
            {prefs.map((pref) => {
              const when = formatSetAt(pref.set_at);
              const isBusy = busyAction === pref.action_id || busyAction === '__all__';
              return (
                <li
                  key={pref.action_id}
                  data-testid={`confirmation-pref-item-${pref.action_id}`}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '8px',
                    flexWrap: 'wrap',
                    padding: '6px 10px',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-subtle)',
                    backgroundColor: 'var(--bg-surface)'
                  }}
                >
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', minWidth: 0 }}>
                    <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>
                      {pref.label_es || pref.action_id}
                    </span>
                    <span style={{ fontSize: '10.5px', color: 'var(--text-muted)' }}>
                      {pref.action_id}
                      {when ? ` · recordado ${when}` : ''}
                    </span>
                  </div>
                  <button
                    type="button"
                    data-testid={`forget-confirmation-${pref.action_id}`}
                    disabled={isBusy}
                    onClick={() => void handleForget(pref.action_id)}
                    style={{
                      padding: '4px 10px',
                      fontSize: '11px',
                      fontWeight: 600,
                      borderRadius: 'var(--radius-sm)',
                      border: '1px solid var(--border-subtle)',
                      backgroundColor: 'var(--bg-subtle)',
                      color: 'var(--text-primary)',
                      cursor: isBusy ? 'not-allowed' : 'pointer'
                    }}
                  >
                    Olvidar
                  </button>
                </li>
              );
            })}
          </ul>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button
              type="button"
              data-testid="forget-all-confirmations"
              disabled={Boolean(busyAction)}
              onClick={() => void handleForgetAll()}
              style={{
                padding: '5px 10px',
                fontSize: '11.5px',
                fontWeight: 600,
                borderRadius: 'var(--radius-sm)',
                border: '1px solid var(--border-subtle)',
                backgroundColor: 'var(--bg-subtle)',
                color: 'var(--text-primary)',
                cursor: busyAction ? 'not-allowed' : 'pointer'
              }}
            >
              Olvidar todas
            </button>
          </div>
        </>
      )}
    </div>
  );
}
