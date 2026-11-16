/**
 * Deteccion de URLs en el buffer de xterm. Se registra como `LinkProvider` de la terminal; al
 * activar un enlace NO se abre nada: se llama a `onOpenUrl`, que muestra el dialogo de
 * confirmacion explicito (anti-phishing) antes de abrir el navegador.
 */

const URL_REGEX = /https?:\/\/[^\s<>"'`)\]]+/g;

function splitTrailingPunctuation(rawUrl) {
  let clean = rawUrl;
  while (clean.length > 0 && /[.,;:!?]/.test(clean[clean.length - 1])) {
    clean = clean.slice(0, -1);
  }
  return clean;
}

/** URLs de un texto: [{ start, end, url }] con `end` exclusivo y sin puntuacion final. */
export function findUrlsInText(text) {
  const found = [];
  if (!text) return found;
  for (const match of String(text).matchAll(URL_REGEX)) {
    const url = splitTrailingPunctuation(match[0]);
    if (url.replace(/^https?:\/\//, '').length > 0) {
      found.push({ start: match.index, end: match.index + url.length, url });
    }
  }
  return found;
}

/**
 * Proveedor de enlaces para `terminal.registerLinkProvider`. Reconstruye la linea logica
 * (varias filas unidas por `isWrapped`) para que una URL larga partida por el ancho del panel
 * siga siendo una sola URL. `bufferLineNumber` es 1-based, como en la API de xterm.
 */
export function createUrlLinkProvider(term, onOpenUrl) {
  return {
    provideLinks(bufferLineNumber, callback) {
      const buffer = term?.buffer?.active;
      if (!buffer || typeof buffer.getLine !== 'function') {
        callback(undefined);
        return;
      }
      const cols = term.cols || 80;
      // Fila inicial de la linea logica (indices 0-based en getLine).
      let first = bufferLineNumber - 1;
      while (first > 0 && buffer.getLine(first)?.isWrapped) first -= 1;
      let text = '';
      let last = first;
      for (let row = first; ; row += 1) {
        const line = buffer.getLine(row);
        if (!line) break;
        last = row;
        const next = buffer.getLine(row + 1);
        const wrapsOn = Boolean(next?.isWrapped);
        // Las filas intermedias se leen completas para conservar las columnas; la ultima, sin cola.
        text += line.translateToString(!wrapsOn).padEnd(wrapsOn ? cols : 0, ' ');
        if (!wrapsOn) break;
      }
      const links = [];
      for (const { start, end, url } of findUrlsInText(text)) {
        const startRow = first + Math.floor(start / cols);
        const endRow = first + Math.floor((end - 1) / cols);
        if (bufferLineNumber - 1 < startRow || bufferLineNumber - 1 > endRow || endRow > last) continue;
        links.push({
          text: url,
          range: {
            start: { x: (start % cols) + 1, y: startRow + 1 },
            end: { x: ((end - 1) % cols) + 1, y: endRow + 1 }
          },
          activate: (_event, linkText) => onOpenUrl?.(linkText || url),
          decorations: { pointerCursor: true, underline: true }
        });
      }
      callback(links.length ? links : undefined);
    }
  };
}
