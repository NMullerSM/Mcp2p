// Logica pura del chat global (sin React): menciones, ticks, agrupacion y colores.
// Todo lo que el modulo necesita del resto de la aplicacion llega por datos; aqui no hay imports externos.

export const MAX_SUGGESTIONS = 8;
export const MAX_MESSAGE_BYTES = 8192;
export const BYTES_WARN_RATIO = 0.8;

// Mencion en curso: un "@" al principio o tras un separador (nunca dentro de una palabra, asi
// "a@b" no dispara la lista) seguido de caracteres de nombre hasta el cursor.
const MENTION_AT_CARET = /(?:^|[^\p{L}\p{N}_@.+-])@([\p{L}\p{N}_.-]*)$/u;

const TODOS_MENTION = '@todos';

/** Minusculas y sin tildes, para comparar nombres escritos a mano. */
export function normalizeText(value) {
  return String(value ?? '')
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase();
}

/**
 * Devuelve `{query, start}` si el cursor esta justo despues de un `@palabra` en edicion
 * (`start` es la posicion del "@"), o null si no hay mencion en curso.
 */
export function mentionQueryAt(text, caret) {
  const value = String(text ?? '');
  if (!Number.isInteger(caret) || caret < 0) return null;
  const before = value.slice(0, Math.min(caret, value.length));
  const match = MENTION_AT_CARET.exec(before);
  if (!match) return null;
  return { query: match[1], start: before.length - match[1].length - 1 };
}

function isTodos(participant) {
  return normalizeText(participant?.mention) === normalizeText(TODOS_MENTION);
}

// `@todos` se ofrece siempre como difusion cuando no se ha escrito nada; si el puente no lo
// listaba, se ofrece igualmente (el envio real lo valida el backend).
const TODOS_PARTICIPANT = Object.freeze({
  principal: 'todos',
  label: 'Todos',
  mention: TODOS_MENTION,
  kind: 'group',
  role: '',
  role_label: 'difusión'
});

/** Participantes que casan con lo escrito tras el "@" (prefijo, sin tildes ni mayusculas). */
export function filterParticipants(participants, query) {
  const list = Array.isArray(participants) ? participants.filter(Boolean) : [];
  const q = normalizeText(String(query ?? '').replace(/^@/, ''));
  const hits = list.filter((p) => {
    const mention = normalizeText(String(p.mention ?? '').replace(/^@/, ''));
    return mention.startsWith(q) || normalizeText(p.label).startsWith(q);
  });
  if (q !== '') return hits.slice(0, MAX_SUGGESTIONS);
  const todos = hits.find(isTodos) || TODOS_PARTICIPANT;
  return [...hits.filter((p) => !isTodos(p)).slice(0, MAX_SUGGESTIONS - 1), todos];
}

/** Sustituye el "@consulta" en edicion por la mencion elegida y un espacio. */
export function applyMention(text, caret, participant) {
  const value = String(text ?? '');
  const mention = String(participant?.mention ?? '');
  const info = mentionQueryAt(value, caret);
  const from = info ? info.start : Math.min(Math.max(caret ?? value.length, 0), value.length);
  const to = info ? caret : from;
  const rest = value.slice(to);
  // Sin "@" en curso la mencion se pega al texto anterior: separarla con un espacio.
  const lead = !info && from > 0 && !/\s/.test(value[from - 1]) ? ' ' : '';
  const separator = /^\s/.test(rest) ? '' : ' ';
  const inserted = lead + mention + separator;
  return {
    text: value.slice(0, from) + inserted + rest,
    caret: from + inserted.length
  };
}

const TICKS = {
  queued: { symbol: '⏳', tone: 'muted' },
  delivering: { symbol: '…', tone: 'muted' },
  delivered: { symbol: '✓', tone: 'ok' },
  read: { symbol: '✓✓', tone: 'ok' },
  replied: { symbol: '↩', tone: 'ok' },
  doubtful: { symbol: '⚠', tone: 'warn' },
  held: { symbol: '🔒', tone: 'warn' },
  expired: { symbol: '✕', tone: 'danger' },
  discarded: { symbol: '✕', tone: 'danger' }
};

/** Simbolo de texto y tono para un estado de entrega. El estado nunca se transmite solo por color. */
export function tickInfo(tick) {
  return TICKS[tick] ? { ...TICKS[tick] } : { symbol: '', tone: 'muted' };
}

/** Los mensajes del humano van a la derecha; el resto, a la izquierda. */
export function bubbleSide(entry) {
  return entry?.sender?.kind === 'human' ? 'right' : 'left';
}

function senderKey(entry) {
  const sender = entry?.sender || {};
  return sender.principal || sender.label || '';
}

/**
 * Agrupa mensajes consecutivos del mismo emisor: `[{sender, entries}]`. Dentro de un grupo,
 * solo el primer mensaje lleva cabecera; el resto son continuaciones.
 */
export function groupConsecutive(entries) {
  const groups = [];
  for (const entry of Array.isArray(entries) ? entries : []) {
    const last = groups[groups.length - 1];
    if (last && senderKey(last.entries[0]) === senderKey(entry)) {
      last.entries.push(entry);
    } else {
      groups.push({ sender: entry?.sender || {}, entries: [entry] });
    }
  }
  return groups;
}

/** Mensajes ajenos al humano posteriores a la ultima vez que vio el chat. */
export function countUnread(entries, lastSeenMs = 0) {
  const seen = Number(lastSeenMs) || 0;
  return (Array.isArray(entries) ? entries : []).filter(
    (e) => e && e.sender?.kind !== 'human' && Number(e.ts_ms) > seen
  ).length;
}

/**
 * Une dos listas por `id`: la version nueva sustituye a la vieja (el tick cambia con el
 * tiempo) y el resultado queda en orden cronologico.
 */
export function mergeEntries(prev, next) {
  const byId = new Map();
  for (const entry of Array.isArray(prev) ? prev : []) {
    if (entry && entry.id != null) byId.set(entry.id, entry);
  }
  for (const entry of Array.isArray(next) ? next : []) {
    if (entry && entry.id != null) byId.set(entry.id, entry);
  }
  return [...byId.values()].sort((a, b) => Number(a.ts_ms) - Number(b.ts_ms));
}

// Ocho tonos a 45 grados: separados entre si y legibles en tema claro y oscuro porque el texto
// usa el color del tema y el fondo es una capa translucida del mismo tono.
const HUES = [0, 45, 90, 135, 180, 225, 270, 315];
export const BUBBLE_COLORS = Object.freeze(
  Object.fromEntries(
    HUES.map((hue, i) => [
      `c${i + 1}`,
      Object.freeze({
        border: `hsl(${hue} 60% 50%)`,
        bg: `hsl(${hue} 60% 50% / 0.16)`
      })
    ])
  )
);

/** Cuantos bytes UTF-8 ocupa el texto (el limite del backend se mide en bytes, no en caracteres). */
export function utf8ByteLength(text) {
  let bytes = 0;
  for (const ch of String(text ?? '')) {
    const code = ch.codePointAt(0);
    if (code < 0x80) bytes += 1;
    else if (code < 0x800) bytes += 2;
    else if (code < 0x10000) bytes += 3;
    else bytes += 4;
  }
  return bytes;
}
