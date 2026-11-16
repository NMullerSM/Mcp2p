import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// Guardas de fuente: el backend elimina el campo duplicado `chunks` de pty_poll_output (EGM-78)
// y la salida ya no se limpia a mano (la interpreta xterm). Si alguien lo reintroduce, esto avisa.

const dir = path.dirname(fileURLToPath(import.meta.url));
const SOURCES = [
  '../SwarmTerminalMatrix.jsx',
  './TerminalPane.jsx',
  './panePoller.js',
  './terminalRegistry.js',
  './terminalModel.js'
].map((rel) => path.resolve(dir, rel));

describe('el frontend de terminales respeta el contrato de pty_poll_output', () => {
  it.each(SOURCES.map((file) => [path.basename(file), file]))('%s no usa el campo chunks', (_name, file) => {
    const text = fs.readFileSync(file, 'utf8');
    expect(text).not.toMatch(/\bchunks\b/);
  });

  it('el panel no conserva el <pre> ni la traduccion casera de teclas', () => {
    const pane = fs.readFileSync(path.resolve(dir, './TerminalPane.jsx'), 'utf8');
    expect(pane).not.toMatch(/<pre\b/);
    expect(pane).not.toMatch(/keyEventToTerminalInput/);
    expect(pane).toMatch(/onData/);
  });

  it('no se usa el addon WebGL (run.sh desactiva el compositing de WebKitGTK)', () => {
    const registry = fs.readFileSync(path.resolve(dir, './terminalRegistry.js'), 'utf8');
    expect(registry).not.toMatch(/addon-webgl|WebglAddon|addon-canvas|CanvasAddon/);
  });
});
