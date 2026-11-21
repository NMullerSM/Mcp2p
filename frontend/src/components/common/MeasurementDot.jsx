import React from 'react';

// Tokens del tema (styles/theme.css). Un color desconocido cae a gris: nunca se pinta un estado inventado.
const DOT_TOKENS = {
  green: 'var(--accent-success)',
  yellow: 'var(--accent-warning)',
  red: 'var(--accent-danger)',
  gray: 'var(--text-muted)'
};

/**
 * Punto de estado de la medición de un CLI. El color por sí solo no basta: `label` es el texto
 * accesible y también el tooltip, así que el estado se lee igual sin distinguir colores.
 */
export function MeasurementDot({ color, label, size = 10 }) {
  const dot = Object.prototype.hasOwnProperty.call(DOT_TOKENS, color) ? color : 'gray';
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-dot={dot}
      style={{
        display: 'inline-block',
        flexShrink: 0,
        width: `${size}px`,
        height: `${size}px`,
        borderRadius: '50%',
        backgroundColor: DOT_TOKENS[dot]
      }}
    />
  );
}
