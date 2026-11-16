// Doble de prueba de @xterm/xterm y @xterm/addon-fit (jsdom no puede renderizar xterm).
// Uso en un test:
//   vi.mock('@xterm/xterm', () => import('./terminals/xtermTestDouble'));
//   vi.mock('@xterm/addon-fit', () => import('./terminals/xtermTestDouble'));
// (el modulo exporta `Terminal` y `FitAddon`; cada mock solo usa el suyo).

export class Terminal {
  static instances = [];

  constructor(options = {}) {
    this.options = options;
    this.cols = options.cols || 80;
    this.rows = options.rows || 24;
    this.written = [];
    this.disposed = false;
    this.opened = false;
    this.selection = '';
    this.lines = [];
    this.dataListeners = new Set();
    this.keyHandler = null;
    this.linkProviders = [];
    this.element = null;
    this.textarea = null;
    this.focusCalls = 0;
    this.buffer = {
      active: {
        getLine: (index) => this.lines[index]
      }
    };
    Terminal.instances.push(this);
  }

  open(parent) {
    this.opened = true;
    this.element = document.createElement('div');
    this.element.className = 'xterm';
    this.textarea = document.createElement('textarea');
    this.textarea.className = 'xterm-helper-textarea';
    this.element.appendChild(this.textarea);
    parent.appendChild(this.element);
  }

  write(data) {
    this.written.push(String(data));
  }

  /** Todo lo escrito hasta ahora, concatenado. */
  get output() {
    return this.written.join('');
  }

  onData(listener) {
    this.dataListeners.add(listener);
    return { dispose: () => this.dataListeners.delete(listener) };
  }

  /** Simula lo que xterm emite cuando la persona teclea o pega. */
  emitData(data) {
    Array.from(this.dataListeners).forEach((listener) => listener(data));
  }

  attachCustomKeyEventHandler(handler) {
    this.keyHandler = handler;
  }

  /** Simula un evento de teclado: devuelve lo que decidio el manejador (true = xterm lo procesa). */
  pressKey(init) {
    const ev = { type: 'keydown', ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...init };
    return this.keyHandler ? this.keyHandler(ev) : true;
  }

  hasSelection() {
    return Boolean(this.selection);
  }

  getSelection() {
    return this.selection;
  }

  focus() {
    this.focusCalls += 1;
    this.textarea?.focus();
  }

  loadAddon(addon) {
    addon.activate(this);
  }

  registerLinkProvider(provider) {
    this.linkProviders.push(provider);
    return { dispose: () => {} };
  }

  resize(cols, rows) {
    this.cols = cols;
    this.rows = rows;
  }

  dispose() {
    this.disposed = true;
    this.dataListeners.clear();
  }
}

export class FitAddon {
  /** Dimensiones que "mide" el panel; null = oculto o sin medir. Las fija el test. */
  static nextDims = null;
  static fitCalls = 0;

  activate(term) {
    this.term = term;
  }

  proposeDimensions() {
    return FitAddon.nextDims;
  }

  fit() {
    FitAddon.fitCalls += 1;
    const dims = FitAddon.nextDims;
    if (dims) this.term.resize(dims.cols, dims.rows);
  }

  dispose() {}
}

export function resetXtermDouble() {
  Terminal.instances.length = 0;
  FitAddon.nextDims = null;
  FitAddon.fitCalls = 0;
}
