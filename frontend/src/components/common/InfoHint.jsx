import React, { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { Info } from 'lucide-react';

/**
 * Ícono de ayuda contextual (ⓘ) con retardo de aparición (~700 ms), soporte de ratón,
 * teclado y toque, y navegación a Ajustes → «Terminales y relevo».
 */
export function InfoHint({
  topic,
  text,
  anchor,
  onOpenSettings,
  delayMs = 700,
  testId
}) {
  const [visible, setVisible] = useState(false);
  const [shiftX, setShiftX] = useState(0);
  const [placeAbove, setPlaceAbove] = useState(false);

  const tooltipId = useId();
  const containerRef = useRef(null);
  const triggerRef = useRef(null);
  const tooltipRef = useRef(null);
  const timerRef = useRef(null);
  const touchHandledRef = useRef(false);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const scheduleShow = () => {
    clearTimer();
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      setVisible(true);
    }, delayMs);
  };

  const hide = () => {
    clearTimer();
    setVisible(false);
  };

  const openSettings = () => {
    hide();
    if (typeof onOpenSettings === 'function') {
      onOpenSettings('terminals_relay', anchor);
    }
  };

  useEffect(() => {
    return () => clearTimer();
  }, []);

  useEffect(() => {
    if (!visible) {
      setShiftX(0);
      setPlaceAbove(false);
      return undefined;
    }
    const onDocKeyDown = (e) => {
      if (e.key === 'Escape') {
        hide();
      }
    };
    document.addEventListener('keydown', onDocKeyDown);
    return () => document.removeEventListener('keydown', onDocKeyDown);
  }, [visible]);

  useLayoutEffect(() => {
    if (!visible || !triggerRef.current || !tooltipRef.current) return;
    const triggerRect = triggerRef.current.getBoundingClientRect();
    const tooltipRect = tooltipRef.current.getBoundingClientRect();
    const vw = window.innerWidth || document.documentElement?.clientWidth || 0;
    const vh = window.innerHeight || document.documentElement?.clientHeight || 0;

    if (vw > 0 && tooltipRect.width > 0) {
      const margin = 16;
      const minLeft = margin;
      const maxLeft = Math.max(margin, vw - tooltipRect.width - margin);
      let desiredLeft = triggerRect.left;
      if (desiredLeft > maxLeft) desiredLeft = maxLeft;
      if (desiredLeft < minLeft) desiredLeft = minLeft;
      setShiftX(Math.round(desiredLeft - triggerRect.left));
    }

    if (vh > 0 && tooltipRect.height > 0) {
      const spaceBelow = vh - triggerRect.bottom - 16;
      const spaceAbove = triggerRect.top - 16;
      if (spaceBelow < tooltipRect.height + 6 && spaceAbove > spaceBelow) {
        setPlaceAbove(true);
      } else {
        setPlaceAbove(false);
      }
    }
  }, [visible, text]);

  const handlePointerDown = (e) => {
    const pType = e.pointerType || e.nativeEvent?.pointerType;
    if (pType === 'touch') {
      if (!visible) {
        touchHandledRef.current = true;
        clearTimer();
        setVisible(true);
      }
    } else {
      touchHandledRef.current = false;
    }
  };

  const handleClick = (e) => {
    e.stopPropagation();
    if (touchHandledRef.current) {
      touchHandledRef.current = false;
      return;
    }
    openSettings();
  };

  const handleFocus = () => {
    if (touchHandledRef.current) return;
    scheduleShow();
  };

  const handleBlur = (e) => {
    if (containerRef.current && e.relatedTarget && containerRef.current.contains(e.relatedTarget)) {
      return;
    }
    touchHandledRef.current = false;
    hide();
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      hide();
      return;
    }
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      e.stopPropagation();
      openSettings();
    }
  };

  return (
    <span
      ref={containerRef}
      onMouseEnter={scheduleShow}
      onMouseLeave={hide}
      onBlur={handleBlur}
      style={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        flexShrink: 0
      }}
    >
      <button
        ref={triggerRef}
        type="button"
        data-testid={testId}
        aria-label={`Ayuda: ${topic}`}
        aria-describedby={visible ? tooltipId : undefined}
        onPointerDown={handlePointerDown}
        onClick={handleClick}
        onFocus={handleFocus}
        onKeyDown={handleKeyDown}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          width: '20px',
          height: '20px',
          padding: 0,
          borderRadius: '999px',
          border: 'none',
          background: 'transparent',
          color: 'var(--text-muted)',
          cursor: 'pointer'
        }}
      >
        <Info size={13} aria-hidden="true" />
      </button>

      {visible && (
        <div
          ref={tooltipRef}
          id={tooltipId}
          role="tooltip"
          style={{
            position: 'absolute',
            left: 0,
            ...(placeAbove ? { bottom: 'calc(100% + 6px)' } : { top: 'calc(100% + 6px)' }),
            transform: shiftX !== 0 ? `translateX(${shiftX}px)` : undefined,
            zIndex: 50,
            width: 'max-content',
            maxWidth: 'min(240px, calc(100vw - 32px))',
            boxSizing: 'border-box',
            padding: '8px 10px',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            backgroundColor: 'var(--bg-surface)',
            color: 'var(--text-primary)',
            boxShadow: 'var(--shadow-md)',
            fontSize: '11.5px',
            lineHeight: 1.35,
            display: 'flex',
            flexDirection: 'column',
            gap: '6px'
          }}
        >
          <span
            style={{
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
              color: 'var(--text-secondary)'
            }}
          >
            {text}
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              openSettings();
            }}
            style={{
              alignSelf: 'flex-start',
              padding: 0,
              border: 'none',
              background: 'transparent',
              color: 'var(--accent-primary)',
              fontSize: '11px',
              fontWeight: 600,
              cursor: 'pointer',
              textDecoration: 'underline'
            }}
          >
            Más información
          </button>
        </div>
      )}
    </span>
  );
}
