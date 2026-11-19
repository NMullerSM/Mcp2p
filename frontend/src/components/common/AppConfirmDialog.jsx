import React, { useEffect, useId, useRef, useState } from 'react';
import { ShieldAlert, X } from 'lucide-react';

const FOCUSABLE_SELECTOR =
  'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';

export function AppConfirmDialog({
  confirmation,
  busy = false,
  onConfirm,
  onCancel,
  armDelayMs = 600
}) {
  const titleId = useId();
  const messageId = useId();
  const checkboxId = useId();
  const dialogRef = useRef(null);
  const cancelBtnRef = useRef(null);
  const previousFocusRef = useRef(null);
  const wasOpenRef = useRef(false);

  const [remember, setRemember] = useState(false);
  const [armed, setArmed] = useState(false);

  const isOpen = Boolean(confirmation && confirmation.token);
  const token = confirmation?.token || '';

  useEffect(() => {
    if (isOpen && !wasOpenRef.current) {
      previousFocusRef.current = typeof document !== 'undefined' ? document.activeElement : null;
      wasOpenRef.current = true;
    } else if (!isOpen && wasOpenRef.current) {
      wasOpenRef.current = false;
      const prev = previousFocusRef.current;
      previousFocusRef.current = null;
      if (prev && typeof prev.focus === 'function') {
        prev.focus();
      }
    }
  }, [isOpen]);

  useEffect(() => {
    return () => {
      if (wasOpenRef.current) {
        wasOpenRef.current = false;
        const prev = previousFocusRef.current;
        previousFocusRef.current = null;
        if (prev && typeof prev.focus === 'function') {
          prev.focus();
        }
      }
    };
  }, []);

  useEffect(() => {
    if (!isOpen) return undefined;
    setRemember(false);
    setArmed(armDelayMs <= 0);
    if (cancelBtnRef.current && typeof cancelBtnRef.current.focus === 'function') {
      cancelBtnRef.current.focus();
    }
    if (armDelayMs <= 0) return undefined;
    const timer = setTimeout(() => setArmed(true), armDelayMs);
    return () => clearTimeout(timer);
  }, [isOpen, token, armDelayMs]);

  if (!isOpen) return null;

  const handleKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      event.stopPropagation();
      if (!busy) onCancel?.();
      return;
    }
    if (event.key !== 'Tab' || !dialogRef.current) return;
    const focusables = Array.from(dialogRef.current.querySelectorAll(FOCUSABLE_SELECTOR));
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (event.shiftKey) {
      if (active === first || !dialogRef.current.contains(active)) {
        event.preventDefault();
        last.focus();
      }
    } else if (active === last || !dialogRef.current.contains(active)) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div
      data-testid="app-confirm-backdrop"
      onClick={() => {
        if (!busy) onCancel?.();
      }}
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.58)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '16px',
        zIndex: 130
      }}
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={messageId}
        data-testid="app-confirm-dialog"
        onKeyDown={handleKeyDown}
        onClick={(event) => event.stopPropagation()}
        style={{
          width: 'min(480px, calc(100vw - 32px))',
          maxWidth: '100%',
          boxSizing: 'border-box',
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
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
          <div
            id={titleId}
            data-testid="app-confirm-title"
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '7px',
              fontSize: '13.5px',
              fontWeight: 700,
              color: 'var(--text-primary)',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word'
            }}
          >
            <ShieldAlert size={15} color="var(--accent-warning)" style={{ flexShrink: 0 }} />
            <span>{confirmation.title}</span>
          </div>
          <button
            type="button"
            aria-label="Cerrar diálogo"
            disabled={busy}
            onClick={() => onCancel?.()}
            style={{
              border: 'none',
              background: 'transparent',
              color: 'var(--text-muted)',
              cursor: busy ? 'not-allowed' : 'pointer',
              display: 'inline-flex',
              flexShrink: 0
            }}
          >
            <X size={15} />
          </button>
        </div>

        <div
          id={messageId}
          data-testid="app-confirm-message"
          style={{
            margin: 0,
            padding: '10px 12px',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-app)',
            border: '1px solid var(--border-subtle)',
            fontSize: '12px',
            lineHeight: 1.5,
            color: 'var(--text-secondary)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            maxHeight: '260px',
            overflowY: 'auto'
          }}
        >
          {confirmation.message}
        </div>

        {Boolean(confirmation.rememberable) && (
          <label
            htmlFor={checkboxId}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              fontSize: '12px',
              color: 'var(--text-primary)',
              cursor: busy ? 'not-allowed' : 'pointer',
              userSelect: 'none'
            }}
          >
            <input
              id={checkboxId}
              type="checkbox"
              data-testid="app-confirm-remember"
              checked={remember}
              disabled={busy}
              onChange={(event) => setRemember(event.target.checked)}
            />
            <span>No volver a preguntar</span>
          </label>
        )}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', flexWrap: 'wrap' }}>
          <button
            ref={cancelBtnRef}
            type="button"
            data-testid="app-confirm-cancel"
            disabled={busy}
            onClick={() => onCancel?.()}
            style={{
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 600,
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              backgroundColor: 'var(--bg-subtle)',
              color: 'var(--text-primary)',
              cursor: busy ? 'not-allowed' : 'pointer'
            }}
          >
            Cancelar
          </button>
          <button
            type="button"
            data-testid="app-confirm-accept"
            disabled={!armed || busy}
            onClick={() => onConfirm?.(Boolean(confirmation.rememberable && remember))}
            style={{
              padding: '6px 12px',
              fontSize: '12px',
              fontWeight: 700,
              borderRadius: 'var(--radius-md)',
              border: 'none',
              backgroundColor: !armed || busy ? 'var(--bg-subtle)' : 'var(--accent-primary)',
              color: !armed || busy ? 'var(--text-muted)' : '#ffffff',
              cursor: !armed || busy ? 'not-allowed' : 'pointer'
            }}
          >
            Confirmar
          </button>
        </div>
      </div>
    </div>
  );
}
