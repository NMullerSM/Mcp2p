// Modelo puro para nodos remotos y tarjetas de cuarentena (v2.0.2, WP-7).

export const NODE_POLICIES = ['direct', 'ask_each', 'ask_once', 'blocked'];

export const POLICY_LABELS_ES = {
  direct: 'Confiar siempre',
  ask_each: 'Preguntar por cada conversación',
  ask_once: 'Preguntar una vez',
  blocked: 'Bloqueado'
};

export const POLICY_OPTIONS = [
  { value: 'direct', label: POLICY_LABELS_ES.direct },
  { value: 'ask_each', label: POLICY_LABELS_ES.ask_each },
  { value: 'ask_once', label: POLICY_LABELS_ES.ask_once },
  { value: 'blocked', label: POLICY_LABELS_ES.blocked }
];

export const RELATION_LABELS_ES = {
  same_owner: 'Mismo dueño',
  foreign: 'Ajeno',
  local: 'Este equipo'
};

export const TRANSPORT_LABELS_ES = {
  simulated: 'simulado',
  p2p: 'P2P',
  relay: 'relevo'
};

export function shortFingerprint(fingerprint) {
  const clean = String(fingerprint || '')
    .replace(/[^a-zA-Z0-9]/g, '')
    .toUpperCase();
  if (!clean) return '';
  if (clean.length >= 16) {
    return `${clean.slice(0, 4)}-${clean.slice(4, 8)}-…-${clean.slice(-8, -4)}-${clean.slice(-4)}`;
  }
  const groups = [];
  for (let i = 0; i < clean.length; i += 4) {
    groups.push(clean.slice(i, i + 4));
  }
  return groups.join('-');
}

export function relationLabel(relation) {
  return RELATION_LABELS_ES[relation] || String(relation || 'Ajeno');
}

export function transportLabel(transport) {
  return TRANSPORT_LABELS_ES[transport] || String(transport || 'desconocido');
}

export function policyLabel(policy) {
  return POLICY_LABELS_ES[policy] || String(policy || 'ask_each');
}
