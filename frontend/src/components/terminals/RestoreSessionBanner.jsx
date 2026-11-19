import React, { useCallback, useEffect, useRef, useState } from 'react';
import { History, RotateCcw } from 'lucide-react';
import { btnBase, mutedText, toneBox } from '../relay/relayStyles';
import { roleLabel } from './roleModel';

const RESUME_LABELS = {
  by_id: 'retoma su conversación',
  continue: 'retoma la última conversación',
  fresh: 'conversación nueva'
};

function shortCwd(cwd) {
  if (typeof cwd !== 'string' || !cwd) return '';
  return cwd.length > 48 ? `…${cwd.slice(-47)}` : cwd;
}

function restorableItems(plan) {
  return Array.isArray(plan?.items) ? plan.items.filter((item) => !item.skip) : [];
}

function messageOf(res, fallback) {
  return typeof res?.message === 'string' && res.message ? res.message : fallback;
}

/**
 * Oferta de restaurar la última sesión del proyecto (WP-4b). Tres estados:
 *  - completo: hay una sesión sin atender y la política es «preguntar»;
 *  - compacto: la sesión ya se descartó o restauró, pero sigue guardada y se puede restaurar a mano;
 *  - nada: no hay nada que restaurar (o la política es «nunca» y no hay sesión a mano).
 * Con la política «automática» restaura sola, una vez por proyecto y por apertura.
 */
export function RestoreSessionBanner({ api, projectUuid, onRestored }) {
  const [policy, setPolicy] = useState('ask');
  const [plan, setPlan] = useState(null);
  const [handled, setHandled] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState(null);
  const mountedRef = useRef(true);
  const autoTriedRef = useRef(new Set());

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const restore = useCallback(async (includeHandled) => {
    setBusy(true);
    setFeedback(null);
    try {
      const res = await api.pty_restore_session(projectUuid, null, includeHandled);
      if (!mountedRef.current) return;
      if (res?.success === false) {
        setFeedback({ tone: 'error', text: messageOf(res, 'No se pudo restaurar la sesión.') });
        return;
      }
      const results = Array.isArray(res?.results) ? res.results : [];
      const failed = results.filter((r) => !r.ok);
      const okCount = results.length - failed.length;
      setFeedback({
        tone: failed.length ? 'warning' : 'ok',
        text: failed.length
          ? `Se restauraron ${okCount} de ${results.length}. ${failed.map((r) => r.message).filter(Boolean).join(' ')}`
          : `Se restauraron ${okCount} terminales.`
      });
      setPlan(null);
      setHandled(true);
      setExpanded(false);
      if (typeof onRestored === 'function') onRestored();
    } catch (err) {
      if (mountedRef.current) {
        setFeedback({ tone: 'error', text: `No se pudo restaurar la sesión: ${err?.message || 'error desconocido'}.` });
      }
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  }, [api, projectUuid, onRestored]);

  useEffect(() => {
    if (typeof api?.pty_get_restorable_session !== 'function') return undefined;
    let cancelled = false;
    setPlan(null);
    setHandled(false);
    setExpanded(false);
    setFeedback(null);
    (async () => {
      try {
        const res = await api.pty_get_restorable_session(projectUuid, false);
        if (cancelled || !mountedRef.current) return;
        const nextPolicy = typeof res?.policy === 'string' ? res.policy : 'ask';
        setPolicy(nextPolicy);
        if (res?.success !== false && restorableItems(res?.plan).length > 0) {
          setPlan(res.plan);
          return;
        }
        // Sin oferta pendiente: ¿sigue guardada una sesión que se pueda restaurar a mano?
        const manual = await api.pty_get_restorable_session(projectUuid, true);
        if (cancelled || !mountedRef.current) return;
        if (manual?.success !== false && restorableItems(manual?.plan).length > 0) {
          setPlan(manual.plan);
          setHandled(true);
        }
      } catch {
        // Sin servicio de sesiones: el banner no aparece.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [api, projectUuid]);

  useEffect(() => {
    if (policy !== 'auto' || handled || !plan || busy) return;
    const key = projectUuid || '';
    if (autoTriedRef.current.has(key)) return;
    autoTriedRef.current.add(key);
    void restore(false);
  }, [policy, handled, plan, busy, projectUuid, restore]);

  const dismiss = async () => {
    setBusy(true);
    try {
      await api.pty_dismiss_restore?.(projectUuid);
      if (!mountedRef.current) return;
      setHandled(true);
      setExpanded(false);
    } finally {
      if (mountedRef.current) setBusy(false);
    }
  };

  const items = restorableItems(plan);
  const skipped = Array.isArray(plan?.items) ? plan.items.filter((item) => item.skip) : [];

  if (feedback && items.length === 0) {
    return (
      <div data-testid="restore-session-feedback" role="status" style={toneBox(feedback.tone)}>
        {feedback.text}
      </div>
    );
  }
  if (items.length === 0) return null;

  const showCompact = handled && !expanded;
  const showFull = !handled || expanded;
  if (policy === 'never' && !handled && !expanded) {
    // La política «nunca» no ofrece sola: queda el acceso compacto a mano.
    return (
      <div data-testid="restore-session-compact" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <button type="button" style={btnBase} onClick={() => setExpanded(true)}>
          <History size={12} />
          <span>Restaurar la última sesión ({items.length})</span>
        </button>
      </div>
    );
  }

  return (
    <div data-testid="restore-session" style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      {feedback && (
        <div data-testid="restore-session-feedback" role="status" style={toneBox(feedback.tone)}>
          {feedback.text}
        </div>
      )}
      {showCompact && (
        <div data-testid="restore-session-compact" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button type="button" style={btnBase} onClick={() => setExpanded(true)} disabled={busy}>
            <History size={12} />
            <span>Restaurar la última sesión ({items.length})</span>
          </button>
        </div>
      )}
      {showFull && (
        <section
          data-testid="restore-session-banner"
          aria-label="Restaurar la última sesión"
          style={{
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            padding: '10px 12px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid rgba(var(--accent-primary-rgb), 0.35)',
            backgroundColor: 'rgba(var(--accent-primary-rgb), 0.07)'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <RotateCcw size={14} color="var(--accent-primary)" aria-hidden="true" />
            <strong style={{ fontSize: '12.5px' }}>
              {plan?.closed_cleanly === false ? 'La app se cerró de golpe. ' : ''}
              Tu última sesión tenía {items.length} {items.length === 1 ? 'terminal' : 'terminales'}.
            </strong>
          </div>
          <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: '3px' }}>
            {items.map((item) => (
              <li key={item.pane_slot} data-testid={`restore-item-${item.pane_slot}`} style={{ ...mutedText, color: 'var(--text-secondary)' }}>
                <strong style={{ color: 'var(--text-primary)' }}>{item.cli_display_name || item.cli_id}</strong>
                {' · '}{roleLabel(item.role)}
                {item.cwd ? <> · <span title={item.cwd} style={{ fontFamily: 'var(--font-mono)' }}>{shortCwd(item.cwd)}</span></> : null}
                {' · '}{RESUME_LABELS[item.resume_mode] || RESUME_LABELS.fresh}
              </li>
            ))}
          </ul>
          {skipped.length > 0 && (
            <div data-testid="restore-skipped" style={toneBox('warning')}>
              {skipped.map((item) => (
                <div key={item.pane_slot}>
                  {(item.warning_details || []).map((w) => w.message).filter(Boolean).join(' ') || `No se puede restaurar ${item.pane_slot}.`}
                </div>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
            <button
              type="button"
              data-testid="restore-session-confirm"
              disabled={busy}
              onClick={() => void restore(handled)}
              style={{ ...btnBase, border: 'none', backgroundColor: 'var(--accent-primary)', color: 'var(--bg-app)' }}
            >
              <span>{busy ? 'Restaurando…' : `Restaurar ${items.length === 1 ? 'la terminal' : `las ${items.length} terminales`}`}</span>
            </button>
            <button
              type="button"
              data-testid="restore-session-dismiss"
              disabled={busy}
              onClick={() => (handled ? setExpanded(false) : void dismiss())}
              style={btnBase}
            >
              <span>{handled ? 'Cerrar' : 'Ahora no'}</span>
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
