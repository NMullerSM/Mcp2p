import React, { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronDown, Lock, RotateCw } from 'lucide-react';
import { InfoHint } from '../common/InfoHint';
import { MandateEditor } from './MandateEditor';
import { paneNumber } from './terminalModel';
import {
  ROLE_HINT_TEXT,
  ROLE_OPTIONS,
  describeRoleError,
  normalizeRole,
  readonlyBadge,
  roleLabel
} from './roleModel';

const FOCUSABLE_SELECTOR =
  'button:not(:disabled), select:not(:disabled), textarea:not(:disabled), input:not(:disabled), [href], [tabindex]:not([tabindex="-1"])';

const chipStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '4px',
  fontFamily: 'var(--font-mono)',
  fontSize: '9.5px',
  fontWeight: 600,
  lineHeight: 1.2,
  padding: '1px 6px',
  borderRadius: '4px',
  border: '1px solid var(--border-subtle)',
  backgroundColor: 'var(--bg-subtle)',
  color: 'var(--text-primary)',
  cursor: 'pointer',
  whiteSpace: 'nowrap',
  pointerEvents: 'auto'
};

// Variante grande para las filas del panel lateral (mismo aspecto que los interruptores de al lado).
const chipRowStyle = {
  ...chipStyle,
  fontFamily: 'inherit',
  fontSize: '11.5px',
  padding: '5px 10px',
  borderRadius: 'var(--radius-md)',
  gap: '5px'
};

const btnBase = {
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  gap: '5px',
  padding: '5px 10px',
  fontSize: '11.5px',
  fontWeight: 600,
  borderRadius: 'var(--radius-sm)',
  border: '1px solid var(--border-subtle)',
  backgroundColor: 'var(--bg-subtle)',
  color: 'var(--text-primary)',
  cursor: 'pointer'
};

const btnPrimary = {
  ...btnBase,
  border: 'none',
  backgroundColor: 'var(--accent-primary)',
  color: 'var(--bg-app)',
  fontWeight: 700
};

/**
 * Chip de rol en la cabecera del panel + popover accesible para asignar rol,
 * redactar el mandato y reiniciar la terminal cuando el backend lo recomienda.
 */
export function RoleChip({ api, pane, onOpenSettings, variant = 'chip' }) {
  if (!pane || pane.kind === 'install') {
    return null;
  }

  return <RoleChipInner api={api} pane={pane} onOpenSettings={onOpenSettings} variant={variant} />;
}

function RoleChipInner({ api, pane, onOpenSettings, variant }) {
  const paneId = pane.paneId;
  const num = paneNumber(paneId) ?? paneId ?? '1';
  const selectId = useId();

  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [relaunching, setRelaunching] = useState(false);
  const [displayedRole, setDisplayedRole] = useState(() => normalizeRole(pane.role));
  const [displayedReadonly, setDisplayedReadonly] = useState(() => pane.readonly || 'none');
  const [savedMandate, setSavedMandate] = useState(() => (typeof pane.mandate === 'string' ? pane.mandate : ''));
  const [draftRole, setDraftRole] = useState(() => normalizeRole(pane.role));
  const [draftMandate, setDraftMandate] = useState(() => (typeof pane.mandate === 'string' ? pane.mandate : ''));
  const [relaunchRecommended, setRelaunchRecommended] = useState(false);
  const [savedFeedback, setSavedFeedback] = useState(false);
  const [error, setError] = useState(null);
  const [position, setPosition] = useState({ top: 32, left: 16 });

  const rootRef = useRef(null);
  const chipRef = useRef(null);
  const dialogRef = useRef(null);
  const selectRef = useRef(null);
  const mountedRef = useRef(true);
  const fetchSeqRef = useRef(0);
  const prevPaneRef = useRef({
    paneId,
    role: pane.role,
    readonly: pane.readonly,
    mandate: pane.mandate
  });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Sincroniza cambios externos del estado de matriz o al reciclar el mismo componente en otro panel.
  useEffect(() => {
    const prev = prevPaneRef.current;
    const paneChanged = prev.paneId !== paneId;
    const roleChanged = prev.role !== pane.role;
    const readonlyChanged = prev.readonly !== pane.readonly;
    const mandateChanged = prev.mandate !== pane.mandate;
    prevPaneRef.current = {
      paneId,
      role: pane.role,
      readonly: pane.readonly,
      mandate: pane.mandate
    };

    if (paneChanged) {
      fetchSeqRef.current += 1;
      setOpen(false);
      setLoading(false);
      setSaving(false);
      setRelaunching(false);
      setError(null);
      setSavedFeedback(false);
      setRelaunchRecommended(false);
      const nextRole = normalizeRole(pane.role);
      const nextMandate = typeof pane.mandate === 'string' ? pane.mandate : '';
      setDisplayedRole(nextRole);
      setDisplayedReadonly(pane.readonly || 'none');
      setSavedMandate(nextMandate);
      setDraftRole(nextRole);
      setDraftMandate(nextMandate);
      return;
    }

    if (roleChanged) {
      const nextRole = normalizeRole(pane.role);
      setDisplayedRole(nextRole);
      if (pane.readonly === undefined) {
        setDisplayedReadonly('none');
      }
    }
    if (readonlyChanged && pane.readonly !== undefined) {
      setDisplayedReadonly(pane.readonly || 'none');
    }
    if (mandateChanged && typeof pane.mandate === 'string') {
      setSavedMandate(pane.mandate);
    }
  }, [paneId, pane.role, pane.readonly, pane.mandate]);

  const closeAndFocusChip = useCallback(() => {
    setOpen(false);
    setError(null);
    setSavedFeedback(false);
    chipRef.current?.focus();
  }, []);

  // Posiciona el popover dentro del viewport (apto para <= 375 px) sin quedar recortado por overflow:hidden del panel.
  useLayoutEffect(() => {
    if (!open || !chipRef.current) return;
    const rect = chipRef.current.getBoundingClientRect();
    const vw = window.innerWidth || document.documentElement?.clientWidth || 375;
    const vh = window.innerHeight || document.documentElement?.clientHeight || 640;
    const margin = 16;
    const popoverW = Math.min(320, Math.max(200, vw - margin * 2));
    const maxLeft = Math.max(margin, vw - popoverW - margin);
    const left = Math.max(margin, Math.min(Math.round(rect.left), maxLeft));

    const dialogH = dialogRef.current?.getBoundingClientRect().height || 260;
    const belowTop = Math.round(rect.bottom + 6);
    const fitsBelow = belowTop + dialogH + margin <= vh;
    const top = fitsBelow || rect.top < dialogH + margin
      ? Math.max(margin, belowTop)
      : Math.max(margin, Math.round(rect.top - dialogH - 6));

    setPosition({ top, left });
  }, [open, error, relaunchRecommended]);

  // Al abrir mueve el foco al selector; Escape y clic fuera cierran el popover.
  useEffect(() => {
    if (!open) return undefined;
    selectRef.current?.focus();

    const onDocMouseDown = (e) => {
      const insideRoot = rootRef.current && rootRef.current.contains(e.target);
      const insideDialog = dialogRef.current && dialogRef.current.contains(e.target);
      if (!insideRoot && !insideDialog) {
        setOpen(false);
        setError(null);
        setSavedFeedback(false);
      }
    };
    const onDocKeyDown = (e) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        closeAndFocusChip();
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    document.addEventListener('keydown', onDocKeyDown);
    return () => {
      document.removeEventListener('mousedown', onDocMouseDown);
      document.removeEventListener('keydown', onDocKeyDown);
    };
  }, [open, closeAndFocusChip]);

  const handleOpen = async (e) => {
    e.stopPropagation();
    if (open) {
      closeAndFocusChip();
      return;
    }

    setDraftRole(displayedRole);
    setDraftMandate(savedMandate);
    setError(null);
    setSavedFeedback(false);
    setOpen(true);

    if (typeof api?.pty_get_pane_role !== 'function') return;

    const seq = ++fetchSeqRef.current;
    setLoading(true);
    try {
      const res = await api.pty_get_pane_role(paneId);
      if (!mountedRef.current || fetchSeqRef.current !== seq) return;
      if (res?.success === false) {
        setError(describeRoleError(res, 'leer el rol'));
        return;
      }
      if (res && typeof res === 'object') {
        const nextRole = normalizeRole(res.role ?? displayedRole);
        const nextMandate = typeof res.mandate === 'string' ? res.mandate : '';
        const nextReadonly = res.readonly || 'none';
        setDisplayedRole(nextRole);
        setDisplayedReadonly(nextReadonly);
        setSavedMandate(nextMandate);
        setDraftRole(nextRole);
        setDraftMandate(nextMandate);
        setRelaunchRecommended(Boolean(res.relaunch_recommended));
      }
    } catch (err) {
      if (mountedRef.current && fetchSeqRef.current === seq) {
        setError(describeRoleError(err, 'leer el rol'));
      }
    } finally {
      if (mountedRef.current && fetchSeqRef.current === seq) {
        setLoading(false);
      }
    }
  };

  const handleSave = async (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (saving || relaunching) return;

    if (typeof api?.pty_set_pane_role !== 'function') {
      setError(describeRoleError({ code: 'ROLE_UNAVAILABLE' }, 'guardar el rol'));
      return;
    }

    setSaving(true);
    setError(null);
    setSavedFeedback(false);
    try {
      const res = await api.pty_set_pane_role(paneId, draftRole, draftMandate);
      if (!mountedRef.current) return;
      if (!res || res.success === false) {
        setError(describeRoleError(res, 'guardar el rol'));
        return;
      }

      const nextRole = normalizeRole(res.role ?? draftRole);
      const nextMandate = typeof res.mandate === 'string' ? res.mandate : draftMandate;
      const nextReadonly = res.readonly || 'none';
      const shouldOfferRelaunch =
        Boolean(res.relaunch_recommended) && typeof api?.pty_relaunch_pane === 'function';

      setDisplayedRole(nextRole);
      setDisplayedReadonly(nextReadonly);
      setSavedMandate(nextMandate);
      setDraftRole(nextRole);
      setDraftMandate(nextMandate);
      setRelaunchRecommended(Boolean(res.relaunch_recommended));

      if (shouldOfferRelaunch) {
        setSavedFeedback(true);
      } else {
        closeAndFocusChip();
      }
    } catch (err) {
      if (mountedRef.current) {
        setError(describeRoleError(err, 'guardar el rol'));
      }
    } finally {
      if (mountedRef.current) {
        setSaving(false);
      }
    }
  };

  const handleRelaunch = async (e) => {
    e.stopPropagation();
    if (relaunching || saving || typeof api?.pty_relaunch_pane !== 'function') return;

    setRelaunching(true);
    setError(null);
    try {
      const res = await api.pty_relaunch_pane(paneId);
      if (!mountedRef.current) return;
      if (res?.success === false) {
        setError(describeRoleError(res, 'reiniciar la terminal'));
        return;
      }
      setRelaunchRecommended(false);
      closeAndFocusChip();
    } catch (err) {
      if (mountedRef.current) {
        setError(describeRoleError(err, 'reiniciar la terminal'));
      }
    } finally {
      if (mountedRef.current) {
        setRelaunching(false);
      }
    }
  };

  // Trampa de foco + Escape dentro del popover.
  const handleDialogKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      closeAndFocusChip();
      return;
    }
    if (e.key !== 'Tab' || !dialogRef.current) return;

    const focusables = Array.from(dialogRef.current.querySelectorAll(FOCUSABLE_SELECTOR));
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];

    if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    } else if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    }
  };

  // La tarjeta y la fila del panel lateral muestran el mismo chip: cada una con su propio identificador.
  const chipIdPrefix = variant === 'row' ? 'role-chip-row' : 'role-chip';
  const label = roleLabel(displayedRole);
  const ro = readonlyBadge(displayedReadonly);
  const canRelaunch = relaunchRecommended && typeof api?.pty_relaunch_pane === 'function';

  return (
    <span
      ref={rootRef}
      onClick={(e) => e.stopPropagation()}
      style={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        pointerEvents: 'auto',
        flexShrink: 0
      }}
    >
      <button
        ref={chipRef}
        type="button"
        data-testid={`${chipIdPrefix}-${paneId}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Rol de Terminal ${num}: ${label}${ro.text ? ` (${ro.text})` : ''}`}
        title={ro.title || `Rol: ${label}. Pulsa para cambiar rol o mandato.`}
        onClick={handleOpen}
        style={variant === 'row' ? chipRowStyle : chipStyle}
      >
        <span>{variant === 'row' ? `Rol: ${label}` : label}</span>
        {ro.mode === 'enforced' && (
          <Lock
            size={10}
            aria-hidden="true"
            data-testid={`${chipIdPrefix}-lock-${paneId}`}
            style={{ color: 'var(--accent-primary)', flexShrink: 0 }}
          />
        )}
        {ro.mode === 'advisory' && (
          <span
            data-testid={`${chipIdPrefix}-advisory-${paneId}`}
            style={{
              fontSize: '9px',
              color: 'var(--accent-warning)',
              fontWeight: 600
            }}
          >
            solo aviso
          </span>
        )}
        <ChevronDown size={variant === 'row' ? 12 : 10} aria-hidden="true" style={{ flexShrink: 0, opacity: 0.7 }} />
      </button>

      {open && createPortal(
        <div
          ref={dialogRef}
          role="dialog"
          aria-label={`Rol de Terminal ${num}`}
          data-testid={`role-popover-${paneId}`}
          onKeyDown={handleDialogKeyDown}
          onClick={(e) => e.stopPropagation()}
          style={{
            position: 'fixed',
            top: `${position.top}px`,
            left: `${position.left}px`,
            width: 'min(320px, calc(100vw - 32px))',
            maxWidth: 'calc(100vw - 32px)',
            zIndex: 1000,
            padding: '12px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            boxShadow: 'var(--shadow-lg)',
            pointerEvents: 'auto',
            userSelect: 'text',
            boxSizing: 'border-box'
          }}
        >
          <form onSubmit={handleSave} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '8px',
                flexWrap: 'wrap'
              }}
            >
              <span style={{ fontSize: '12px', fontWeight: 700, color: 'var(--text-primary)' }}>
                Rol de Terminal {num}
              </span>
              <InfoHint
                topic="Roles"
                text={ROLE_HINT_TEXT}
                anchor="ayuda-roles"
                onOpenSettings={onOpenSettings}
                testId={`role-hint-${paneId}`}
              />
            </div>

            {loading && (
              <p
                data-testid={`role-loading-${paneId}`}
                style={{ margin: 0, fontSize: '11px', color: 'var(--text-muted)' }}
              >
                Cargando configuración de rol…
              </p>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              <label
                htmlFor={selectId}
                style={{ fontSize: '11.5px', fontWeight: 600, color: 'var(--text-secondary)' }}
              >
                Rol
              </label>
              <select
                ref={selectRef}
                id={selectId}
                data-testid={`role-select-${paneId}`}
                className="mcp-combobox"
                value={draftRole}
                disabled={saving || relaunching}
                onChange={(e) => setDraftRole(e.target.value)}
                style={{
                  width: '100%',
                  height: '28px',
                  fontSize: '11.5px',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-subtle)',
                  backgroundColor: 'var(--bg-app)',
                  color: 'var(--text-primary)',
                  padding: '0 8px'
                }}
              >
                {ROLE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            <MandateEditor
              value={draftMandate}
              onChange={setDraftMandate}
              disabled={saving || relaunching}
              testIdPrefix={`role-mandate-${paneId}`}
            />

            {error && (
              <p
                role="alert"
                data-testid={`role-error-${paneId}`}
                style={{
                  margin: 0,
                  padding: '6px 8px',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--accent-danger)',
                  backgroundColor: 'var(--bg-subtle)',
                  color: 'var(--accent-danger)',
                  fontSize: '11px',
                  lineHeight: 1.35
                }}
              >
                {error}
              </p>
            )}

            {canRelaunch && (
              <div
                data-testid={`role-relaunch-offer-${paneId}`}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '6px',
                  padding: '8px',
                  borderRadius: 'var(--radius-sm)',
                  border: '1px solid var(--border-subtle)',
                  backgroundColor: 'var(--bg-subtle)'
                }}
              >
                {savedFeedback && (
                  <span style={{ fontSize: '11px', color: 'var(--accent-success)', fontWeight: 600 }}>
                    Rol guardado.
                  </span>
                )}
                <button
                  type="button"
                  data-testid={`role-relaunch-btn-${paneId}`}
                  disabled={relaunching || saving}
                  onClick={handleRelaunch}
                  style={{
                    ...btnBase,
                    width: '100%',
                    textAlign: 'left',
                    justifyContent: 'flex-start',
                    fontSize: '11px',
                    color: 'var(--accent-primary)',
                    backgroundColor: 'var(--bg-app)',
                    cursor: relaunching || saving ? 'wait' : 'pointer'
                  }}
                >
                  <RotateCw size={12} style={{ flexShrink: 0 }} />
                  <span>
                    {relaunching
                      ? 'Reiniciando terminal…'
                      : 'Reiniciar la terminal para aplicarlo (reanuda la conversación)'}
                  </span>
                </button>
              </div>
            )}

            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'flex-end',
                gap: '8px',
                flexWrap: 'wrap'
              }}
            >
              <button
                type="button"
                data-testid={`role-cancel-btn-${paneId}`}
                disabled={saving || relaunching}
                onClick={closeAndFocusChip}
                style={btnBase}
              >
                Cancelar
              </button>
              <button
                type="submit"
                data-testid={`role-save-btn-${paneId}`}
                disabled={saving || relaunching}
                style={{ ...btnPrimary, cursor: saving || relaunching ? 'wait' : 'pointer' }}
              >
                {saving ? 'Guardando…' : 'Guardar'}
              </button>
            </div>
          </form>
        </div>,
        document.body
      )}
    </span>
  );
}
