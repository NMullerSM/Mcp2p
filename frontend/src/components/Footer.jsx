import React from 'react';

export function Footer() {
  return (
    <footer style={{
      height: '28px',
      backgroundColor: 'var(--bg-surface)',
      borderTop: '1px solid var(--border-subtle)',
      display: 'flex',
      alignItems: 'center',
      padding: '0 16px',
      fontSize: '11px',
      color: 'var(--text-secondary)',
      fontFamily: 'var(--font-mono)',
      flexShrink: 0
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
        <span style={{
          display: 'inline-block',
          width: '6px',
          height: '6px',
          borderRadius: '50%',
          backgroundColor: 'var(--accent-success)',
          boxShadow: '0 0 6px var(--accent-success)'
        }} />
        <span>SQLite WAL Activo</span>
      </div>
    </footer>
  );
}
