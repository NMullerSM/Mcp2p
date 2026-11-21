// Modelo puro de la entrega directa (sin React): textos de los motivos, etiqueta del interruptor y ciclo de modos.

export const DELIVERY_MODES = ['auto', 'direct', 'inbox'];

// Codigo del backend (mode_reason / now_reason) -> texto en espanol. Nunca se muestra el codigo crudo.
export const DELIVERY_REASON_TEXT = Object.freeze({
  GLOBAL_PAUSED: 'El relevo esta en pausa global.',
  PANE_PAUSED: 'Esta terminal esta en pausa.',
  PANE_GONE: 'La terminal ya no esta activa.',
  DELIVERY_MODE_INBOX: 'Esta terminal recibe solo por la bandeja.',
  CLI_NOT_VERIFIED: 'Este CLI aun no esta medido: solo bandeja.',
  TARGET_ELEVATED: 'Terminal con permisos saltados: solo bandeja.',
  AGENT_BUSY: 'El agente esta trabajando.',
  AGENT_PERMISSION: 'El agente espera un permiso.',
  AGENT_STATE_UNKNOWN: 'Aun no se sabe si el agente esta libre.',
  AWAITING_IDLE_AFTER_INJECTION: 'Esperando a que el agente termine el turno anterior.',
  RECENT_OUTPUT: 'La terminal acaba de escribir.',
  HUMAN_TYPING: 'Estas escribiendo en esta terminal.',
  IN_FLIGHT: 'Hay una entrega en curso.',
  REMOTE_POLICY: 'Mensaje de otro equipo sin permiso de entrega directa.',
  MESSAGE_EXPIRED: 'El mensaje caduco.',
  TOO_LONG_POINTER: 'Mensaje largo: se avisa y se lee por la bandeja.'
});

const TOGGLE_LABELS = Object.freeze({
  direct: 'Entrega directa: ON',
  inbox: 'Entrega directa: OFF',
  auto: 'Entrega directa: AUTO'
});

const PLATFORM_LABELS = Object.freeze({
  linux: 'Linux',
  darwin: 'macOS',
  win32: 'Windows'
});

function normalizeMode(mode) {
  return DELIVERY_MODES.includes(mode) ? mode : 'auto';
}

/** Texto del motivo, o '' si el codigo es desconocido o vacio. */
export function describeDeliveryReason(code) {
  if (typeof code !== 'string') return '';
  return Object.prototype.hasOwnProperty.call(DELIVERY_REASON_TEXT, code) ? DELIVERY_REASON_TEXT[code] : '';
}

/** Etiqueta del interruptor. Un modo ausente cuenta como AUTO. `effective` no cambia la etiqueta: el motivo va aparte. */
export function deliveryToggleLabel(mode, _effective) {
  return TOGGLE_LABELS[normalizeMode(mode)];
}

/** Siguiente modo del ciclo auto -> direct -> inbox -> auto. */
export function nextDeliveryMode(mode) {
  const index = DELIVERY_MODES.indexOf(normalizeMode(mode));
  return DELIVERY_MODES[(index + 1) % DELIVERY_MODES.length];
}

/** Nombre legible del sistema operativo de un CLI medido. */
export function describeDeliveryPlatform(platform) {
  return Object.prototype.hasOwnProperty.call(PLATFORM_LABELS, platform) ? PLATFORM_LABELS[platform] : 'Otro sistema';
}
