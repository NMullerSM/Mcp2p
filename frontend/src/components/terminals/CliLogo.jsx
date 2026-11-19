import React from 'react';

/**
 * Logotipos discretos de los CLIs del catalogo. Son monogramas SVG propios (sin imagenes
 * externas ni dependencias: la CSP solo admite 'self' y data:). No pretenden copiar la marca
 * oficial, solo distinguir un panel de otro de un vistazo.
 */

export const CLI_ICON_IDS = ['claude', 'codex', 'opencode', 'antigravity', 'cursor', 'shell'];

const ICON_ALIASES = {
  custom_shell: 'shell',
  agy: 'antigravity'
};

/** Id de icono a partir del `icon` del catalogo y, si falta, del id del CLI. */
export function resolveCliIconId(cliId, icon) {
  for (const candidate of [icon, cliId]) {
    if (!candidate) continue;
    const key = ICON_ALIASES[candidate] || candidate;
    if (CLI_ICON_IDS.includes(key)) return key;
  }
  return 'generic';
}

const TINTS = {
  claude: '#c8765a',
  codex: '#8f9aa8',
  opencode: '#9aa3ad',
  antigravity: '#a58bd4',
  cursor: '#6c9cf0',
  shell: '#7f8a96',
  generic: '#7f8a96'
};

function Glyph({ iconId, letter }) {
  switch (iconId) {
    case 'claude':
      // Destello de ocho puntas.
      return (
        <g stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
          <line x1="12" y1="3.5" x2="12" y2="20.5" />
          <line x1="3.5" y1="12" x2="20.5" y2="12" />
          <line x1="6" y1="6" x2="18" y2="18" />
          <line x1="18" y1="6" x2="6" y2="18" />
        </g>
      );
    case 'codex':
      // Hexagono con prompt.
      return (
        <g fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 2.8l8 4.6v9.2l-8 4.6-8-4.6V7.4z" />
          <path d="M9 10l3 2-3 2" />
          <line x1="13.5" y1="14.5" x2="16" y2="14.5" />
        </g>
      );
    case 'opencode':
      // Corchetes de codigo abierto.
      return (
        <g fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
          <path d="M8.5 5.5L3.5 12l5 6.5" />
          <path d="M15.5 5.5l5 6.5-5 6.5" />
        </g>
      );
    case 'antigravity':
      // Pico ascendente con linea de flotacion.
      return (
        <g fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
          <path d="M4.5 18.5L12 4.5l7.5 14" />
          <line x1="8" y1="21" x2="16" y2="21" />
        </g>
      );
    case 'cursor':
      // Puntero de cursor.
      return (
        <g fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 4l14 7-6 2-2 6z" />
        </g>
      );
    case 'shell':
      // Prompt >_
      return (
        <g fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M5 7l6 5-6 5" />
          <line x1="13" y1="18" x2="19" y2="18" />
        </g>
      );
    default:
      return (
        <g>
          <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" />
          <text
            x="12"
            y="16"
            textAnchor="middle"
            fontSize="11"
            fontWeight="700"
            fill="currentColor"
            fontFamily="monospace"
          >
            {letter}
          </text>
        </g>
      );
  }
}

export function CliLogo({ cliId, icon, name, size = 14, muted = true, style, ...rest }) {
  const iconId = resolveCliIconId(cliId, icon);
  const letter = String(name || cliId || '?').trim().charAt(0).toUpperCase() || '?';
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      role="img"
      aria-label={name ? `Logotipo de ${name}` : 'Logotipo del CLI'}
      data-icon={iconId}
      style={{
        color: TINTS[iconId] || TINTS.generic,
        opacity: muted ? 0.85 : 1,
        flexShrink: 0,
        display: 'block',
        ...style
      }}
      {...rest}
    >
      <Glyph iconId={iconId} letter={letter} />
    </svg>
  );
}
