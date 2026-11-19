import { describe, it, expect, afterEach } from 'vitest';
import {
  computeGridLayout,
  pickFreePaneId,
  computePollDelay,
  POLL_FAST_MS,
  POLL_WARM_MS,
  POLL_IDLE_MS,
  POLL_HIDDEN_MS,
  stripAnsiSequences,
  describeTerminalError,
  describeInstallBlock,
  canOfferInstall,
  rememberPaneSession,
  recallPaneSession,
  resetPaneSessionMemory,
  MATRIX_PANE_IDS
} from './terminalModel';

afterEach(() => resetPaneSessionMemory());

describe('computeGridLayout', () => {
  it.each([
    [0, 1, 1],
    [1, 1, 1],
    [2, 2, 1],
    [3, 2, 2],
    [4, 2, 2],
    [5, 3, 2],
    [6, 3, 2]
  ])('con %i terminales abiertas la cuadricula es %i columnas x %i filas', (count, cols, rows) => {
    const layout = computeGridLayout(count);
    expect(layout.cols).toBe(cols);
    expect(layout.rows).toBe(rows);
    expect(layout.cells).toBe(cols * rows);
    expect(layout.columns).toBe(`repeat(${cols}, minmax(0, 1fr))`);
  });

  it('no pasa del tope de 6 paneles', () => {
    expect(computeGridLayout(99).cells).toBe(6);
    expect(MATRIX_PANE_IDS).toHaveLength(6);
  });
});

describe('pickFreePaneId', () => {
  it('elige el primer pane libre dentro del maximo', () => {
    expect(pickFreePaneId([], 4)).toBe('pane_1');
    expect(pickFreePaneId(['pane_1', 'pane_3'], 4)).toBe('pane_2');
    expect(pickFreePaneId(['pane_1', 'pane_2', 'pane_3', 'pane_4'], 4)).toBeNull();
    expect(pickFreePaneId(['pane_1', 'pane_2', 'pane_3', 'pane_4'], 6)).toBe('pane_5');
  });
});

describe('texto del terminal', () => {
  it('quita secuencias ANSI y de conjunto de caracteres (solo para texto plano fuera de xterm)', () => {
    expect(stripAnsiSequences('\x1b[32mok\x1b[0m \x1b(B\x1b]0;titulo\x07fin')).toBe('ok fin');
  });

  it('ya no hay limpieza casera de la salida: la interpreta el emulador', async () => {
    const model = await import('./terminalModel');
    expect(model.renderTerminalText).toBeUndefined();
  });
});

describe('computePollDelay (sondeo adaptativo)', () => {
  const NOW = 1_000_000;

  it('va rapido (~30 ms) mientras haya salida reciente o se acabe de teclear', () => {
    expect(computePollDelay({ now: NOW, lastActivityAt: NOW })).toBe(POLL_FAST_MS);
    expect(computePollDelay({ now: NOW, lastActivityAt: NOW - 1400 })).toBe(POLL_FAST_MS);
    expect(POLL_FAST_MS).toBeLessThanOrEqual(30);
  });

  it('retrocede de forma escalonada hasta ~300 ms en reposo', () => {
    expect(computePollDelay({ now: NOW, lastActivityAt: NOW - 2500 })).toBe(POLL_WARM_MS);
    expect(computePollDelay({ now: NOW, lastActivityAt: NOW - 4000 })).toBe(POLL_IDLE_MS);
    expect(computePollDelay({ now: NOW, lastActivityAt: 0 })).toBe(POLL_IDLE_MS);
    expect(POLL_IDLE_MS).toBeGreaterThanOrEqual(250);
    expect(POLL_IDLE_MS).toBeLessThanOrEqual(350);
  });

  it('con la vista oculta sondea despacio aunque haya actividad', () => {
    expect(computePollDelay({ now: NOW, lastActivityAt: NOW, hidden: true })).toBe(POLL_HIDDEN_MS);
    expect(POLL_HIDDEN_MS).toBeGreaterThan(POLL_IDLE_MS);
  });
});

describe('memoria de sesiones', () => {
  it('solo devuelve la carpeta si la sesion es la misma', () => {
    rememberPaneSession('pane_1', { sessionId: 's1', cwd: '/tmp/a' });
    expect(recallPaneSession('pane_1', 's1').cwd).toBe('/tmp/a');
    expect(recallPaneSession('pane_1', 's2')).toBeNull();
    expect(recallPaneSession('pane_2', 's1')).toBeNull();
  });
});

describe('describeTerminalError', () => {
  it('CLI_BINARY_NOT_FOUND ofrece instalar si install_available y siempre configurar la ruta', () => {
    const e = describeTerminalError(
      { code: 'CLI_BINARY_NOT_FOUND', install_available: true, cli_profile: 'claude', message: 'No se encontro claude' },
      { cliName: 'Claude Code' }
    );
    expect(e.title).toContain('Claude Code');
    expect(e.actions).toEqual([{ kind: 'install', cliId: 'claude' }, { kind: 'configure_path', cliId: 'claude' }]);
    expect(e.hint).toContain('instalar');
  });

  it('CLI_BINARY_NOT_FOUND sin instalacion solo ofrece configurar la ruta y explica lo que falta', () => {
    const e = describeTerminalError({
      code: 'CLI_BINARY_NOT_FOUND',
      install_available: false,
      install_missing_requirements: ['npm'],
      cli_profile: 'codex'
    });
    expect(e.actions).toEqual([{ kind: 'configure_path', cliId: 'codex' }]);
    expect(e.hint).toContain('npm');
  });

  it('CLI_PROFILE_UNKNOWN lista los perfiles permitidos', () => {
    const e = describeTerminalError({ code: 'CLI_PROFILE_UNKNOWN', known_profiles: ['claude', 'codex'] });
    expect(e.title).toMatch(/Perfil de CLI/);
    expect(e.hint).toContain('claude, codex');
  });

  it('CLI_PROFILE_UNKNOWN para gemini redirige a Antigravity', () => {
    const e = describeTerminalError({
      code: 'CLI_PROFILE_UNKNOWN',
      cli_profile: 'gemini',
      known_profiles: ['claude', 'codex', 'opencode', 'antigravity', 'custom_shell']
    });
    expect(e.message).toContain('Gemini CLI se retiró del catálogo; abre Antigravity en su lugar');
  });

  it.each([
    ['pane_out_of_range', /fuera de rango/i, /pane_6/],
    ['max_panes', /ya hay 4 terminales/i, /cierra una terminal/i],
    ['low_memory', /memoria/i, /libera memoria/i]
  ])('PANE_LIMIT_REACHED (%s) tiene su propio mensaje', (reason, titleRx, hintRx) => {
    const e = describeTerminalError({ code: 'PANE_LIMIT_REACHED', reason, max_panes: reason === 'pane_out_of_range' ? 6 : 4 });
    expect(e.title).toMatch(titleRx);
    expect(e.hint).toMatch(hintRx);
  });

  it('INVALID_CWD pide una ruta absoluta existente', () => {
    const e = describeTerminalError({ code: 'INVALID_CWD', message: 'no existe' });
    expect(e.title).toMatch(/Carpeta de trabajo/);
    expect(e.hint).toMatch(/ruta absoluta/);
  });

  it('PREREQUISITE_MISSING dice que falta', () => {
    const e = describeTerminalError({ code: 'PREREQUISITE_MISSING', missing: ['npm', 'node'] });
    expect(e.hint).toContain('npm, node');
  });

  it('TERMINAL_PLATFORM_UNSUPPORTED explica Windows y trae el comando manual', () => {
    const e = describeTerminalError({ code: 'TERMINAL_PLATFORM_UNSUPPORTED', manual_command: 'mcp2p --mcp-server' });
    expect(e.title).toMatch(/Windows/);
    expect(e.manualCommand).toBe('mcp2p --mcp-server');
    expect(e.actions).toEqual([{ kind: 'show_manual' }]);
  });

  it.each(['TERMINAL_ADAPTER_DEGRADED', 'PTY_UNAVAILABLE'])('%s no usa el mensaje generico de degradado', (code) => {
    const e = describeTerminalError({ code });
    expect(e.title).toMatch(/conector de terminales no está disponible/);
    expect(e.manualCommand).toContain('mcp2p');
    expect(e.title.toLowerCase()).not.toContain('degradado');
  });

  it('un codigo desconocido muestra el mensaje del backend', () => {
    const e = describeTerminalError({ code: 'ALGO_NUEVO', message: 'hola' });
    expect(e.code).toBe('ALGO_NUEVO');
    expect(e.message).toBe('hola');
  });
});

describe('CONFIRMATION_DECLINED (dialogo nativo rechazado)', () => {
  it('es un aviso informativo sin acciones ni tono de error', () => {
    const e = describeTerminalError({ success: false, code: 'CONFIRMATION_DECLINED', message: 'Cancelaste la instalacion.' });
    expect(e.code).toBe('CONFIRMATION_DECLINED');
    expect(e.tone).toBe('info');
    expect(e.title).toMatch(/Cancelaste/);
    expect(e.message).toBe('Cancelaste la instalacion.');
    expect(e.hint).toMatch(/confirmación/);
    expect(e.actions).toEqual([]);
  });

  it('sin mensaje del backend usa uno propio', () => {
    expect(describeTerminalError({ code: 'CONFIRMATION_DECLINED' }).message).toMatch(/ningún cambio/);
  });
});

describe('reglas de instalacion', () => {
  const ok = { id: 'claude', installed: false, kind: 'agent_cli', verified: true, install: { available: true, verified: true } };

  it('ofrece instalar solo si el CLI esta verificado y el instalador disponible y verificado', () => {
    expect(canOfferInstall(ok)).toBe(true);
    expect(canOfferInstall({ ...ok, verified: false })).toBe(false);
    expect(canOfferInstall({ ...ok, install: { available: false, verified: true } })).toBe(false);
    expect(canOfferInstall({ ...ok, install: { available: true, verified: false } })).toBe(false);
    expect(canOfferInstall({ ...ok, installed: true })).toBe(false);
    expect(canOfferInstall({ ...ok, kind: 'shell' })).toBe(false);
  });

  it('explica por que no hay boton', () => {
    expect(describeInstallBlock({ ...ok, verified: false, source: 'user' })).toMatch(/no está verificado/);
    expect(describeInstallBlock({ ...ok, install: { available: false, missing_requirements: ['npm'] } })).toMatch(/npm/);
    expect(describeInstallBlock({ ...ok, install: { available: false } })).toMatch(/instálalo manualmente/);
    expect(describeInstallBlock(ok)).toBeNull();
  });

  it('describeInstallBlock_windows_without_method_names_the_cli', () => {
    const msg = describeInstallBlock({
      id: 'opencode',
      display_name: 'OpenCode',
      installed: false,
      kind: 'agent_cli',
      verified: true,
      install: { available: false, code: 'NO_INSTALL_METHOD', command: null, method: null }
    });
    expect(msg).toBe('No hay un método de instalación verificado de OpenCode para este sistema: instálalo manualmente.');
  });
});
