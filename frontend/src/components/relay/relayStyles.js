// Estilos compartidos por los componentes del relevo (mismo lenguaje visual que el slider de terminales).
export const btnBase = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: '5px',
  padding: '5px 10px',
  fontSize: '11.5px',
  fontWeight: 600,
  borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border-subtle)',
  backgroundColor: 'var(--bg-subtle)',
  color: 'var(--text-primary)',
  cursor: 'pointer'
};
export const chip = {
  padding: '1px 6px',
  borderRadius: 'var(--radius-sm)',
  fontFamily: 'var(--font-mono)',
  fontSize: '10px',
  backgroundColor: 'var(--bg-subtle)',
  color: 'var(--text-secondary)',
  border: '1px solid var(--border-subtle)'
};
export const sectionTitle = {
  margin: 0,
  fontSize: '11px',
  fontWeight: 700,
  letterSpacing: '0.04em',
  textTransform: 'uppercase',
  color: 'var(--text-secondary)'
};
export const mutedText = { fontSize: '11.5px', color: 'var(--text-muted)', lineHeight: 1.4 };
export const fieldStyle = {
  padding: '5px 9px',
  borderRadius: 'var(--radius-sm)',
  border: '1px solid var(--border-subtle)',
  backgroundColor: 'var(--bg-app)',
  color: 'var(--text-primary)',
  fontFamily: 'var(--font-mono)',
  fontSize: '11.5px',
  outline: 'none',
  minWidth: 0
};
export const TONES = {
  error: { color: 'var(--accent-danger)', bg: 'rgba(239, 68, 68, 0.1)', border: 'rgba(239, 68, 68, 0.35)' },
  warning: { color: 'var(--accent-warning)', bg: 'rgba(245, 158, 11, 0.1)', border: 'rgba(245, 158, 11, 0.38)' },
  ok: { color: 'var(--accent-success)', bg: 'rgba(16, 185, 129, 0.1)', border: 'rgba(16, 185, 129, 0.35)' },
  info: { color: 'var(--text-secondary)', bg: 'var(--bg-subtle)', border: 'var(--border-subtle)' }
};
export function toneBox(tone) {
  const t = TONES[tone] || TONES.info;
  return {
    padding: '6px 9px',
    borderRadius: 'var(--radius-sm)',
    fontSize: '11.5px',
    lineHeight: 1.4,
    color: t.color,
    backgroundColor: t.bg,
    border: `1px solid ${t.border}`
  };
}
