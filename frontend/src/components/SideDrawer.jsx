import React, { useState, useEffect, useRef } from 'react';
import { X } from 'lucide-react';

/**
 * Drawer lateral derecho reutilizable (mismo lenguaje visual que SettingsDrawer):
 * oculto por defecto, se desliza desde la derecha al abrirse explícitamente y
 * contiene el contenido SECUNDARIO/menos usado de la vista activa, dejando la
 * vista principal despejada. Reemplaza el patrón previo de "Vista Dual (Split)".
 */
export function SideDrawer({
  isOpen,
  onClose,
  title,
  subtitle,
  width = 'clamp(320px, 42vw, 560px)',
  'data-testid': testId,
  children
}) {
  const [isHidden, setIsHidden] = useState(!isOpen);
  const bodyRef = useRef(null);

  useEffect(() => {
    if (isOpen) setIsHidden(false);
  }, [isOpen]);

  // Resetear el scroll del cuerpo cada vez que el drawer se abre de nuevo.
  useEffect(() => {
    if (isOpen && bodyRef.current) {
      bodyRef.current.scrollTop = 0;
    }
  }, [isOpen]);

  return (
    <>
      <div
        onClick={onClose}
        data-testid={testId ? `${testId}-backdrop` : undefined}
        style={{
          position: 'fixed',
          inset: 0,
          backgroundColor: 'rgba(0, 0, 0, 0.45)',
          opacity: isOpen ? 1 : 0,
          pointerEvents: isOpen ? 'auto' : 'none',
          transition: 'opacity 0.2s ease',
          zIndex: 90
        }}
      />
      <aside
        data-testid={testId}
        onTransitionEnd={() => {
          if (!isOpen) setIsHidden(true);
        }}
        style={{
          position: 'fixed',
          top: 0,
          right: 0,
          width,
          maxWidth: '96vw',
          height: '100vh',
          backgroundColor: 'var(--bg-surface)',
          borderLeft: '1px solid var(--border-subtle)',
          boxShadow: 'var(--shadow-lg)',
          transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
          transition: 'transform 0.28s cubic-bezier(0.16, 1, 0.3, 1)',
          willChange: 'transform',
          contain: 'layout paint style',
          visibility: (!isOpen && isHidden) ? 'hidden' : 'visible',
          display: 'flex',
          flexDirection: 'column',
          zIndex: 100
        }}
      >
        <div style={{
          padding: '16px 20px',
          borderBottom: '1px solid var(--border-subtle)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          backgroundColor: 'var(--bg-surface)',
          flexShrink: 0
        }}>
          <div style={{ minWidth: 0 }}>
            <h3 style={{ fontSize: '15px', fontWeight: 700, margin: 0 }}>{title}</h3>
            {subtitle && (
              <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{subtitle}</span>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar panel"
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              padding: '6px',
              borderRadius: 'var(--radius-sm)',
              flexShrink: 0
            }}
          >
            <X size={18} />
          </button>
        </div>

        <div ref={bodyRef} style={{ flex: 1, overflowY: 'auto', padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {children}
        </div>
      </aside>
    </>
  );
}
