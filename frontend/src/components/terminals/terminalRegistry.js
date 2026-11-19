import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';
import { createUrlLinkProvider } from './terminalLinks';

/**
 * Registro de instancias de xterm, UNA por panel y FUERA de React.
 *
 * Por que fuera: un TUI (claude, codex, opencode...) entra en pantalla alterna (ESC[?1049h) una sola vez
 * al arrancar. Si la instancia se destruyera al cambiar de pestana o de layout, al volver habria
 * que repetir la salida desde el principio y el ring buffer del backend, ya truncado, habria
 * perdido esa entrada: la pantalla quedaria corrupta. Aqui la instancia (y el cursor
 * `nextSeq` de sondeo) sobreviven a cualquier desmontaje de componentes; solo se destruyen al
 * cerrar el panel o al abrir otra sesion en el mismo panel.
 *
 * Solo se usa el renderizador DOM de xterm (el por defecto): run.sh desactiva el compositing
 * de WebKitGTK, asi que nada de WebGL.
 */

const entries = new Map();

const TERMINAL_THEME = {
  background: '#000000',
  foreground: '#d4d4d4',
  cursor: '#d4d4d4',
  cursorAccent: '#000000',
  selectionBackground: 'rgba(106, 169, 255, 0.35)'
};
const FALLBACK_FONT = '"JetBrains Mono", "Fira Code", ui-monospace, SFMono-Regular, Consolas, monospace';

function monoFontFamily() {
  try {
    const value = getComputedStyle(document.documentElement).getPropertyValue('--font-mono').trim();
    return value || FALLBACK_FONT;
  } catch {
    return FALLBACK_FONT;
  }
}

/**
 * Devuelve la instancia del panel o crea una. Si ya hay una para OTRA sesion (el panel se
 * reutiliza), se destruye antes. Un `sessionId` vacio en cualquiera de los lados se considera
 * compatible: el id llega a veces despues de abrir el panel.
 */
export function acquireTerminal(paneId, { sessionId = '', cols = 100, rows = 28 } = {}) {
  const current = entries.get(paneId);
  if (current) {
    const compatible = !current.sessionId || !sessionId || current.sessionId === sessionId;
    if (compatible) {
      if (!current.sessionId && sessionId) current.sessionId = sessionId;
      return current;
    }
    disposeTerminal(paneId);
  }

  const term = new Terminal({
    cols,
    rows,
    scrollback: 5000,
    cursorBlink: true,
    fontSize: 12,
    lineHeight: 1.15,
    fontFamily: monoFontFamily(),
    theme: TERMINAL_THEME
  });
  const fit = new FitAddon();
  term.loadAddon(fit);

  const host = document.createElement('div');
  host.className = 'mcp-xterm-host';
  host.setAttribute('data-pane', paneId);

  const entry = {
    paneId,
    sessionId,
    term,
    fit,
    host,
    opened: false,
    disposed: false,
    // Siguiente seq que se pide a pty_poll_output (cursor de lectura de esta sesion).
    nextSeq: 0,
    // Ultima salida recibida o tecla enviada (ms); marca el ritmo del sondeo.
    lastActivityAt: 0,
    exitAnnounced: false,
    // Ultimo tamano conocido del PTY; evita llamar a pty_resize_pane si no cambio.
    ptySize: { cols, rows },
    // Lo fija el panel montado; abre el dialogo de confirmacion de URL externa.
    onOpenUrl: null
  };
  term.registerLinkProvider(createUrlLinkProvider(term, (url) => entry.onOpenUrl?.(url)));
  entries.set(paneId, entry);
  return entry;
}

export function getTerminalEntry(paneId) {
  return entries.get(paneId) || null;
}

/** Cuelga el elemento de la terminal en `container` (y la abre la primera vez). */
export function attachTerminal(entry, container) {
  if (!entry || entry.disposed || !container) return;
  if (entry.host.parentNode !== container) container.appendChild(entry.host);
  if (!entry.opened) {
    entry.term.open(entry.host);
    entry.opened = true;
  }
}

/** Saca el elemento de la terminal del DOM sin destruir la instancia. */
export function detachTerminal(entry, container) {
  if (!entry) return;
  if (entry.host.parentNode && (!container || entry.host.parentNode === container)) {
    entry.host.parentNode.removeChild(entry.host);
  }
}

export function disposeTerminal(paneId) {
  const entry = entries.get(paneId);
  if (!entry) return;
  entries.delete(paneId);
  entry.disposed = true;
  entry.onOpenUrl = null;
  detachTerminal(entry);
  try {
    entry.term.dispose();
  } catch {
    // La instancia ya estaba destruida.
  }
}

export function disposeAllTerminals() {
  Array.from(entries.keys()).forEach(disposeTerminal);
}

// Mensajes discretos escritos DENTRO de la terminal (gris tenue, en su propia linea).
const DIM = '\x1b[2m';
const RESET = '\x1b[0m';

export function writeTerminalNotice(entry, message) {
  if (!entry || entry.disposed) return;
  entry.term.write(`\r\n${DIM}[${message}]${RESET}\r\n`);
}
