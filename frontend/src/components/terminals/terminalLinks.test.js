import { describe, it, expect } from 'vitest';
import { createUrlLinkProvider, findUrlsInText } from './terminalLinks';

function fakeTerm(rows, cols = 20) {
  return {
    cols,
    buffer: {
      active: {
        getLine: (i) => {
          const row = rows[i];
          if (!row) return undefined;
          return {
            isWrapped: Boolean(row.wrapped),
            translateToString: (trimRight) => (trimRight ? row.text.replace(/\s+$/, '') : row.text.padEnd(cols, ' '))
          };
        }
      }
    }
  };
}

function provide(term, y) {
  const opened = [];
  const provider = createUrlLinkProvider(term, (url) => opened.push(url));
  let links;
  provider.provideLinks(y, (found) => { links = found; });
  return { links, opened };
}

describe('findUrlsInText', () => {
  it('encuentra URLs sin la puntuacion final', () => {
    expect(findUrlsInText('ver https://a.example/x, y http://b.example.')).toEqual([
      { start: 4, end: 23, url: 'https://a.example/x' },
      { start: 27, end: 43, url: 'http://b.example' }
    ]);
    expect(findUrlsInText('sin enlaces')).toEqual([]);
    expect(findUrlsInText('https://.')).toEqual([]);
    expect(findUrlsInText('')).toEqual([]);
  });
});

describe('proveedor de enlaces de xterm', () => {
  it('devuelve el enlace con su rango (1-based) y activarlo solo pide abrirlo', () => {
    const term = fakeTerm([{ text: 'abre https://x.io/a hoy' }], 30);
    const { links, opened } = provide(term, 1);
    expect(links).toHaveLength(1);
    expect(links[0].text).toBe('https://x.io/a');
    expect(links[0].range).toEqual({ start: { x: 6, y: 1 }, end: { x: 19, y: 1 } });
    links[0].activate({}, links[0].text);
    expect(opened).toEqual(['https://x.io/a']); // el padre decide (dialogo de confirmacion)
  });

  it('una URL partida por el ancho del panel (filas envueltas) sigue siendo una sola', () => {
    const term = fakeTerm([
      { text: 'ir a https://example' },
      { text: '.com/login?x=1 fin', wrapped: true }
    ], 20);
    for (const y of [1, 2]) {
      const { links } = provide(term, y);
      expect(links).toHaveLength(1);
      expect(links[0].text).toBe('https://example.com/login?x=1');
      expect(links[0].range.start).toEqual({ x: 6, y: 1 });
      expect(links[0].range.end).toEqual({ x: 14, y: 2 });
    }
  });

  it('sin URL en la fila no devuelve enlaces, y sin buffer tampoco falla', () => {
    expect(provide(fakeTerm([{ text: 'nada que ver' }]), 1).links).toBeUndefined();
    expect(provide({ cols: 80 }, 1).links).toBeUndefined();
  });

  it('una URL de otra fila no se ofrece en esta', () => {
    const term = fakeTerm([{ text: 'https://uno.example' }, { text: 'texto normal' }], 30);
    expect(provide(term, 2).links).toBeUndefined();
  });
});
