import { ROLE_OPTIONS } from './terminalModel';

export { ROLE_OPTIONS };

export const DEFAULT_ROLE = 'peer';
export const MAX_MANDATE_CHARS = 2000;
export const MANDATE_SECRET_WARNING =
  'No pongas secretos: el mandato se pasa al CLI al lanzarlo.';
export const MANDATE_INSTRUCTION_NOTE =
  'El mandato es una instrucción, no un candado.';
export const ROLE_HINT_TEXT =
  'El rol lo eliges tú y decide qué tipos de mensaje puede enviar cada agente.';

const LEGACY_ROLE_MAP = {
  orchestrator: 'orchestrator',
  implementer: 'implementer',
  reviewer: 'auditor',
  auditor: 'auditor',
  mediator: 'mediator',
  coordinator: 'peer',
  peer: 'peer'
};

const ROLE_LABELS_BY_VALUE = Object.fromEntries(
  ROLE_OPTIONS.map((opt) => [opt.value, opt.label])
);

/** Normaliza roles legados o desconocidos al conjunto 2.0.2 sin escalar privilegios. */
export function normalizeRole(role) {
  if (typeof role !== 'string') return DEFAULT_ROLE;
  return LEGACY_ROLE_MAP[role.trim().toLowerCase()] || DEFAULT_ROLE;
}

/** Etiqueta en español del rol normalizado. */
export function roleLabel(role) {
  const norm = normalizeRole(role);
  return ROLE_LABELS_BY_VALUE[norm] || 'Par';
}

/** Metadatos visuales de solo lectura para el chip según `readonly` del backend. */
export function readonlyBadge(readonly) {
  if (readonly === 'enforced') {
    return {
      mode: 'enforced',
      text: 'Solo lectura impuesto',
      title: 'Solo lectura impuesto por el CLI'
    };
  }
  if (readonly === 'advisory') {
    return {
      mode: 'advisory',
      text: 'solo aviso',
      title: 'Este CLI no impone solo lectura: el rol es solo aviso'
    };
  }
  return { mode: 'none', text: '', title: '' };
}

const ROLE_ERROR_HELP = {
  INVALID_ARGUMENT: 'Faltan datos o no son válidos. Revise la selección.',
  PANE_NOT_FOUND: 'La terminal ya no está abierta. Actualice la lista de terminales.',
  INTERNAL_ERROR: 'Error interno al gestionar el rol. Vuelva a intentarlo.',
  CONFIRMATION_DECLINED: 'Se canceló la confirmación, así que no se hizo ningún cambio.',
  CONFIRMATION_UNAVAILABLE: 'No se pudo abrir la confirmación del sistema.',
  PTY_SPAWN_FAILED: 'No se pudo reiniciar la terminal. Revise la carpeta de trabajo y reintente.',
  CLI_BINARY_NOT_FOUND: 'No se encontró el binario del CLI para reiniciar la terminal.',
  CWD_MISSING: 'La carpeta de trabajo ya no existe.',
  ROLE_UNAVAILABLE: 'La gestión de roles no está disponible en esta sesión.'
};

/** «No se pudo {action}: causa y qué hacer» equivalente a `describeRelayError`. */
export function describeRoleError(res, action = 'guardar el rol') {
  const code = res && typeof res === 'object' ? res.code : undefined;
  const raw = res instanceof Error ? res.message : res?.message || res?.error || '';
  const help = (code && ROLE_ERROR_HELP[code]) || raw || 'Causa desconocida. Vuelva a intentarlo.';
  return `No se pudo ${action}: ${help}`;
}
