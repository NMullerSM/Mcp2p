// Modelo puro de la UI del relevo entre agentes (sin React): textos de error con causa y accion, etiquetas y formato.

export const MAX_MESSAGE_BYTES = 8 * 1024;

// Cada codigo del backend -> que paso y que hacer. Si el codigo no esta aqui se usa el mensaje del backend tal cual.
const ERROR_HELP = {
  RELAY_UNAVAILABLE: 'El relevo no está disponible en esta sesión. Reinicie MCP2P e inténtelo de nuevo.',
  CONFIRMATION_DECLINED: 'Rechazó el diálogo de confirmación del sistema, así que no se hizo ningún cambio.',
  CONFIRMATION_UNAVAILABLE: 'No se pudo abrir el diálogo de confirmación del sistema, así que no se hizo ningún cambio.',
  MESSAGE_NOT_FOUND: 'Ese mensaje ya no está retenido (otra acción lo resolvió). Actualice la bandeja.',
  INVALID_ARGUMENT: 'Faltan datos o no son válidos. Revise el formulario.',
  TARGET_NOT_FOUND: 'La terminal de destino ya no está abierta. Ábrala de nuevo o elija otra.',
  PROJECT_SCOPE_FORBIDDEN: 'Esa terminal pertenece a otro proyecto. Elija una terminal del proyecto activo.',
  EMPTY_MESSAGE: 'Escriba el mensaje antes de enviarlo.',
  MESSAGE_TOO_LARGE: 'El mensaje supera los 8 KiB. Acórtelo o divídalo en varios.',
  INVALID_KIND: 'El tipo de mensaje no es válido. Elija «nota» o «tarea».',
  INBOX_FULL: 'La bandeja del destino está llena. Espere a que el agente la vacíe.',
  PANE_NOT_FOUND: 'La terminal ya no está abierta. Actualice la lista de terminales.',
  PANE_NOT_PAUSED: 'Esa terminal no tenía el relevo en pausa.',
  INTERNAL_ERROR: 'Error interno del relevo. Vuelva a intentarlo; si persiste, revise el registro de MCP2P.'
};

const REASON_LABELS = {
  CHAIN_DEPTH_EXCEEDED: 'Cadena de respuestas automáticas agotada',
  HUMAN_CONFIRMATION_REQUESTED: 'El agente pidió su confirmación'
};

export function reasonLabel(reason) {
  if (!reason) return 'Retenido para el humano';
  return REASON_LABELS[reason] || String(reason);
}

/** «No se pudo {action}: causa y qué hacer». `res` es la respuesta del puente (o una excepción). */
export function describeRelayError(res, action) {
  const code = res && typeof res === 'object' ? res.code : undefined;
  const raw = res instanceof Error ? res.message : res?.message || res?.error || '';
  const help = (code && ERROR_HELP[code]) || raw || 'Causa desconocida. Vuelva a intentarlo.';
  return `No se pudo ${action}: ${help}`;
}

export function shortId(value, size = 8) {
  const text = String(value || '');
  return text.length > size ? text.slice(0, size) : text;
}

/** Nombre legible de un remitente/destino del sobre: agente y terminal, o «Usted» para el humano. */
export function partyLabel(party) {
  if (!party) return 'Desconocido';
  if (party.kind === 'human') return 'Usted';
  if (party.kind === 'operator') return party.label || 'Operador';
  const name = party.agent_name || shortId(party.node_uuid, 12) || 'agente';
  return party.pane_id ? `${name} (${party.pane_id})` : name;
}

export function formatWhen(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return '';
  try {
    return new Date(n).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' });
  } catch {
    return '';
  }
}

export function utf8Length(text) {
  try {
    return new TextEncoder().encode(String(text || '')).length;
  } catch {
    return String(text || '').length;
  }
}

const STORAGE_LABELS = { keyring: 'llavero del sistema', encrypted_file: 'archivo cifrado' };
export function storageLabel(storage) {
  return STORAGE_LABELS[storage] || storage || 'desconocido';
}
