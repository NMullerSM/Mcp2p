import { describe, it, expect } from 'vitest';
import {
  mentionQueryAt,
  filterParticipants,
  applyMention,
  tickInfo,
  bubbleSide,
  groupConsecutive,
  countUnread,
  mergeEntries,
  BUBBLE_COLORS,
  utf8ByteLength,
  MAX_SUGGESTIONS
} from './chatModel';

const PARTICIPANTS = [
  { principal: 'agent:orq', label: 'Orquestador', mention: '@orquestador', kind: 'agent', role: 'orchestrator', role_label: 'orquestador' },
  { principal: 'agent:claude', label: 'Claude', mention: '@claude', kind: 'agent', role: 'worker', role_label: 'trabajador' },
  { principal: 'agent:t2', label: 'Terminal 2', mention: '@terminal2', kind: 'agent', role: 'worker', role_label: 'trabajador' },
  { principal: 'human:me', label: 'Ana Gómez', mention: '@ana', kind: 'human', role: '', role_label: '' }
];

describe('mentionQueryAt', () => {
  it('detecta el @palabra que se escribe justo antes del cursor', () => {
    expect(mentionQueryAt('hola @cla', 9)).toEqual({ query: 'cla', start: 5 });
  });

  it('un @ solo al inicio o tras espacio abre la lista con consulta vacia', () => {
    expect(mentionQueryAt('@', 1)).toEqual({ query: '', start: 0 });
    expect(mentionQueryAt('uno @', 5)).toEqual({ query: '', start: 4 });
  });

  it('no dispara dentro de una palabra con @ en medio (un correo)', () => {
    expect(mentionQueryAt('escribe a@b', 11)).toBeNull();
    expect(mentionQueryAt('a@', 2)).toBeNull();
  });

  it('solo mira el texto hasta el cursor y se corta en el primer espacio', () => {
    expect(mentionQueryAt('@claude hola', 3)).toEqual({ query: 'cl', start: 0 });
    expect(mentionQueryAt('@claude hola', 9)).toBeNull();
  });

  it('devuelve null sin mencion o con cursor invalido', () => {
    expect(mentionQueryAt('texto sin mencion', 5)).toBeNull();
    expect(mentionQueryAt('@x', -1)).toBeNull();
    expect(mentionQueryAt('@x', 1.5)).toBeNull();
  });

  it('admite letras acentuadas en la consulta', () => {
    expect(mentionQueryAt('@ana', 4)?.query).toBe('ana');
    expect(mentionQueryAt('@Ñu', 3)?.query).toBe('Ñu');
  });
});

describe('filterParticipants', () => {
  it('filtra por prefijo sin mayusculas ni tildes, sobre mencion y etiqueta', () => {
    expect(filterParticipants(PARTICIPANTS, 'CLA').map((p) => p.mention)).toEqual(['@claude']);
    expect(filterParticipants(PARTICIPANTS, 'ana go').map((p) => p.mention)).toEqual(['@ana']);
    expect(filterParticipants(PARTICIPANTS, 'ANÁ').map((p) => p.mention)).toEqual(['@ana']);
    expect(filterParticipants(PARTICIPANTS, 'terminal').map((p) => p.mention)).toEqual(['@terminal2']);
  });

  it('no encuentra coincidencias en medio de la palabra', () => {
    expect(filterParticipants(PARTICIPANTS, 'udia')).toEqual([]);
  });

  it('con consulta vacia ofrece @todos siempre, aunque el puente no lo listara', () => {
    const mentions = filterParticipants(PARTICIPANTS, '').map((p) => p.mention);
    expect(mentions[mentions.length - 1]).toBe('@todos');
    expect(mentions.filter((m) => m === '@todos')).toHaveLength(1);
  });

  it('no duplica @todos si el puente ya lo lista', () => {
    const withTodos = [...PARTICIPANTS, { principal: 'group:all', label: 'Todos', mention: '@todos', kind: 'group', role: '', role_label: '' }];
    const mentions = filterParticipants(withTodos, '').map((p) => p.mention);
    expect(mentions.filter((m) => m === '@todos')).toHaveLength(1);
  });

  it('limita a 8 sugerencias y conserva @todos con consulta vacia', () => {
    const many = Array.from({ length: 20 }, (_, i) => ({ principal: `a${i}`, label: `Agente ${i}`, mention: `@ag${i}`, kind: 'agent' }));
    expect(filterParticipants(many, '')).toHaveLength(MAX_SUGGESTIONS);
    expect(filterParticipants(many, '')[MAX_SUGGESTIONS - 1].mention).toBe('@todos');
    expect(filterParticipants(many, 'ag')).toHaveLength(MAX_SUGGESTIONS);
  });

  it('tolera entradas nulas o sin lista', () => {
    expect(filterParticipants(null, 'x')).toEqual([]);
    expect(filterParticipants([null, PARTICIPANTS[1]], 'cl')).toHaveLength(1);
  });
});

describe('applyMention', () => {
  it('sustituye el @consulta por la mencion y un espacio', () => {
    const result = applyMention('hola @cl', 8, PARTICIPANTS[1]);
    expect(result).toEqual({ text: 'hola @claude ', caret: 13 });
  });

  it('no añade espacio si ya lo hay tras el cursor', () => {
    const text = 'hola @cl resto';
    const result = applyMention(text, 8, PARTICIPANTS[1]);
    expect(result.text).toBe('hola @claude resto');
    expect(result.caret).toBe(12);
  });

  it('sin mencion en curso inserta la mencion separada del texto anterior', () => {
    expect(applyMention('hola', 4, PARTICIPANTS[0])).toEqual({ text: 'hola @orquestador ', caret: 18 });
  });
});

describe('tickInfo', () => {
  it('asigna simbolo y tono a cada estado', () => {
    expect(tickInfo('queued')).toEqual({ symbol: '⏳', tone: 'muted' });
    expect(tickInfo('delivering')).toEqual({ symbol: '…', tone: 'muted' });
    expect(tickInfo('delivered')).toEqual({ symbol: '✓', tone: 'ok' });
    expect(tickInfo('read')).toEqual({ symbol: '✓✓', tone: 'ok' });
    expect(tickInfo('replied')).toEqual({ symbol: '↩', tone: 'ok' });
    expect(tickInfo('doubtful')).toEqual({ symbol: '⚠', tone: 'warn' });
    expect(tickInfo('held')).toEqual({ symbol: '🔒', tone: 'warn' });
    expect(tickInfo('expired')).toEqual({ symbol: '✕', tone: 'danger' });
    expect(tickInfo('discarded')).toEqual({ symbol: '✕', tone: 'danger' });
  });

  it('un estado desconocido no rompe la vista', () => {
    expect(tickInfo('otro')).toEqual({ symbol: '', tone: 'muted' });
    expect(tickInfo(undefined)).toEqual({ symbol: '', tone: 'muted' });
  });
});

describe('bubbleSide', () => {
  it('el humano a la derecha, los demas a la izquierda', () => {
    expect(bubbleSide({ sender: { kind: 'human' } })).toBe('right');
    expect(bubbleSide({ sender: { kind: 'agent' } })).toBe('left');
    expect(bubbleSide({ sender: { kind: 'system' } })).toBe('left');
    expect(bubbleSide({})).toBe('left');
  });
});

describe('groupConsecutive', () => {
  const e = (id, principal) => ({ id, ts_ms: Number(id), sender: { principal, label: principal, kind: 'agent' } });

  it('agrupa solo mensajes consecutivos del mismo emisor', () => {
    const groups = groupConsecutive([e('1', 'a'), e('2', 'a'), e('3', 'b'), e('4', 'a')]);
    expect(groups.map((g) => g.entries.map((x) => x.id))).toEqual([['1', '2'], ['3'], ['4']]);
    expect(groups[0].sender.principal).toBe('a');
  });

  it('tolera lista vacia o ausente', () => {
    expect(groupConsecutive([])).toEqual([]);
    expect(groupConsecutive(undefined)).toEqual([]);
  });
});

describe('countUnread', () => {
  const entries = [
    { id: '1', ts_ms: 100, sender: { kind: 'agent' } },
    { id: '2', ts_ms: 200, sender: { kind: 'human' } },
    { id: '3', ts_ms: 300, sender: { kind: 'agent' } },
    { id: '4', ts_ms: 400, sender: { kind: 'system' } }
  ];

  it('cuenta lo posterior a lastSeenMs que no es del humano', () => {
    expect(countUnread(entries, 150)).toBe(2);
    expect(countUnread(entries, 0)).toBe(3);
    expect(countUnread(entries, 400)).toBe(0);
  });

  it('sin lastSeenMs cuenta todo lo ajeno', () => {
    expect(countUnread(entries)).toBe(3);
    expect(countUnread(null)).toBe(0);
  });
});

describe('mergeEntries', () => {
  it('une por id: la version nueva sustituye a la vieja y ordena por ts_ms', () => {
    const prev = [
      { id: 'b', ts_ms: 20, tick: 'queued' },
      { id: 'a', ts_ms: 10, tick: 'delivered' }
    ];
    const next = [
      { id: 'b', ts_ms: 20, tick: 'read' },
      { id: 'c', ts_ms: 30, tick: 'queued' }
    ];
    const merged = mergeEntries(prev, next);
    expect(merged.map((x) => x.id)).toEqual(['a', 'b', 'c']);
    expect(merged[1].tick).toBe('read');
  });

  it('no muta las listas de entrada y tolera nulos', () => {
    const prev = [{ id: 'a', ts_ms: 1 }];
    mergeEntries(prev, [null, { id: 'b', ts_ms: 0 }]);
    expect(prev).toEqual([{ id: 'a', ts_ms: 1 }]);
    expect(mergeEntries(undefined, undefined)).toEqual([]);
  });
});

describe('BUBBLE_COLORS', () => {
  it('define c1..c8 con bordes distintos y formato hsl', () => {
    const keys = ['c1', 'c2', 'c3', 'c4', 'c5', 'c6', 'c7', 'c8'];
    const borders = keys.map((k) => BUBBLE_COLORS[k].border);
    expect(new Set(borders).size).toBe(8);
    for (const k of keys) {
      expect(BUBBLE_COLORS[k].border).toMatch(/^hsl\(\d+ 60% 50%\)$/);
      expect(BUBBLE_COLORS[k].bg).toMatch(/^hsl\(\d+ 60% 50% \/ 0\.16\)$/);
    }
    expect(BUBBLE_COLORS.c9).toBeUndefined();
  });
});

describe('utf8ByteLength', () => {
  it('cuenta bytes UTF-8, no caracteres', () => {
    expect(utf8ByteLength('abc')).toBe(3);
    expect(utf8ByteLength('ñ')).toBe(2);
    expect(utf8ByteLength('€')).toBe(3);
    expect(utf8ByteLength('😀')).toBe(4);
    expect(utf8ByteLength('')).toBe(0);
    expect(utf8ByteLength(null)).toBe(0);
  });
});
