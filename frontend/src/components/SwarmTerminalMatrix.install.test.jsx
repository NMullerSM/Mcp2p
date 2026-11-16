// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';
import { resetPaneSessionMemory } from './terminals/terminalModel';
import {
  MANUAL_CMD,
  makeCatalogResponse,
  makeCli,
  makeMatrixApi,
  notInstalled,
  renderMatrix,
  resetTerminalTestState,
  termOf
} from './terminals/terminalTestKit';

// jsdom no renderiza xterm: se sustituye por un doble que registra lo que se le escribe.
vi.mock('@xterm/xterm', () => import('./terminals/xtermTestDouble'));
vi.mock('@xterm/addon-fit', () => import('./terminals/xtermTestDouble'));

const TOKEN = 'tok-secreto-8f3a21';
// Comando con caracteres que un armado en el frontend podria "limpiar": debe mostrarse tal cual.
const EXACT_COMMAND = 'npm install -g @example/gemini-cli@1.2.3  # --registry=https://registry.example.com';

afterEach(() => {
  cleanup();
  resetTerminalTestState();
  resetPaneSessionMemory();
  window.localStorage.clear();
  window.sessionStorage.clear();
  vi.restoreAllMocks();
});

function prepareResponse(over = {}) {
  return {
    success: true,
    code: 'OK',
    requires_confirmation: true,
    cli_id: 'gemini',
    display_name: 'Gemini',
    command: EXACT_COMMAND,
    method: 'npm',
    downloads_script: false,
    warning: '',
    requires: ['npm'],
    platform: 'linux',
    source_url: 'https://example.com/gemini',
    note: '',
    confirmation_token: TOKEN,
    expires_in_s: 120,
    ...over
  };
}

function installApi(over = {}) {
  const state = { installAlive: true };
  const api = makeMatrixApi({
    pty_list_cli_catalog: vi.fn(async () => makeCatalogResponse([
      makeCli('claude'),
      notInstalled('gemini'),
      makeCli('custom_shell')
    ])),
    pty_prepare_install: vi.fn(async () => prepareResponse()),
    pty_install_cli: vi.fn(async () => ({
      success: true,
      code: 'INSTALL_STARTED',
      cli_id: 'gemini',
      pane_id: 'pane_1',
      session_id: 'install-sess-1',
      pid: 5151,
      command: EXACT_COMMAND
    })),
    pty_poll_output: vi.fn(async (paneId, sinceSeq = 0) => ({
      success: true,
      pane_id: paneId,
      alive: state.installAlive,
      paused: false,
      rss_mb: 3,
      next_seq: sinceSeq === 0 ? 1 : sinceSeq,
      truncated: false,
      text: sinceSeq === 0 ? 'added 120 packages\n' : '',
      exit_code: state.installAlive ? undefined : 0
    })),
    ...over
  });
  return { api, state };
}

describe('Instalar un CLI que falta', () => {
  it('prepare -> muestra el comando EXACTO -> confirmar -> install con el token -> panel con la salida -> refresca el catalogo', async () => {
    const { api, state } = installApi();
    renderMatrix(api);

    fireEvent.click(await screen.findByTestId('cli-install-gemini'));
    await waitFor(() => expect(api.pty_prepare_install).toHaveBeenCalledWith('gemini'));

    const confirm = await screen.findByTestId('install-confirm-gemini');
    expect(within(confirm).getByTestId('install-command').textContent).toBe(EXACT_COMMAND);
    expect(within(confirm).getByTestId('install-method').textContent).toContain('npm');
    expect(within(confirm).getByTestId('install-requires').textContent).toContain('npm');
    expect(within(confirm).getByTestId('install-source').textContent).toContain('https://example.com/gemini');
    // Todavia no se instala nada y el token nunca llega a la pantalla ni a ningun almacen.
    expect(api.pty_install_cli).not.toHaveBeenCalled();
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(TOKEN);
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(TOKEN);

    const catalogCallsBefore = api.pty_list_cli_catalog.mock.calls.length;
    fireEvent.click(screen.getByTestId('install-confirm-btn'));
    await waitFor(() => expect(api.pty_install_cli).toHaveBeenCalledTimes(1));
    expect(api.pty_install_cli).toHaveBeenCalledWith('gemini', TOKEN);
    expect(screen.queryByTestId('install-confirm-gemini')).toBeNull();

    // El backend abre un panel real con la salida de la instalacion.
    const pane = await screen.findByTestId('terminal-pane-pane_1');
    expect(within(pane).getByTestId('pane-logo-pane_1').getAttribute('data-cli')).toBe('custom_shell');
    await waitFor(() => expect(termOf('pane_1').output).toContain('added 120 packages'));

    // Al terminar el instalador se vuelve a leer el catalogo.
    expect(api.pty_list_cli_catalog.mock.calls.length).toBe(catalogCallsBefore);
    state.installAlive = false;
    await waitFor(() => expect(api.pty_list_cli_catalog.mock.calls.length).toBeGreaterThan(catalogCallsBefore));
    await waitFor(() => expect(termOf('pane_1').output).toContain('proceso terminado, código 0'));
  });

  it('sin confirmar nunca se llama a pty_install_cli; cancelar descarta el token y exige preparar otra vez', async () => {
    const { api } = installApi();
    renderMatrix(api);

    fireEvent.click(await screen.findByTestId('cli-install-gemini'));
    await screen.findByTestId('install-confirm-gemini');
    await new Promise((r) => setTimeout(r, 60));
    expect(api.pty_install_cli).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('install-cancel-btn'));
    expect(screen.queryByTestId('install-confirm-gemini')).toBeNull();
    expect(api.pty_install_cli).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('cli-install-gemini'));
    await waitFor(() => expect(api.pty_prepare_install).toHaveBeenCalledTimes(2));
    expect(api.pty_install_cli).not.toHaveBeenCalled();
  });

  it('el token es de un solo uso: tras confirmar no queda nada que reenviar', async () => {
    const { api } = installApi();
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-install-gemini'));
    fireEvent.click(await screen.findByTestId('install-confirm-btn'));
    await waitFor(() => expect(api.pty_install_cli).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('install-confirm-btn')).toBeNull();
    await new Promise((r) => setTimeout(r, 50));
    expect(api.pty_install_cli).toHaveBeenCalledTimes(1);
  });

  it('avisa explicitamente cuando el instalador es un script descargado y exige reconocerlo', async () => {
    const { api } = installApi({
      pty_prepare_install: vi.fn(async () => prepareResponse({
        command: 'curl -fsSL https://example.com/install.sh | bash',
        method: 'script',
        downloads_script: true,
        warning: 'El instalador oficial es un script descargado de Internet y ejecutado en su shell.'
      }))
    });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-install-gemini'));

    expect((await screen.findByTestId('install-script-warning')).textContent).toMatch(/descarga un script de Internet/);
    expect(screen.getByTestId('install-warning').textContent).toMatch(/script descargado/);
    expect(screen.getByTestId('install-command').textContent).toBe('curl -fsSL https://example.com/install.sh | bash');

    const confirmBtn = screen.getByTestId('install-confirm-btn');
    expect(confirmBtn.disabled).toBe(true);
    fireEvent.click(confirmBtn);
    expect(api.pty_install_cli).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId('install-script-ack'));
    expect(screen.getByTestId('install-confirm-btn').disabled).toBe(false);
    fireEvent.click(screen.getByTestId('install-confirm-btn'));
    await waitFor(() => expect(api.pty_install_cli).toHaveBeenCalledWith('gemini', TOKEN));
  });

  it('un CLI con verified:false no ofrece instalar y explica por que', async () => {
    const { api } = installApi({
      pty_list_cli_catalog: vi.fn(async () => makeCatalogResponse([
        notInstalled('propio', {}, { verified: false, source: 'user' }),
        makeCli('custom_shell')
      ]))
    });
    renderMatrix(api);
    await screen.findByTestId('cli-row-propio');
    expect(screen.queryByTestId('cli-install-propio')).toBeNull();
    expect(screen.getByTestId('cli-install-block-propio').textContent).toMatch(/no está verificado/);
    expect(screen.getByTestId('cli-row-propio').textContent).toContain('No verificado');
  });

  it('sin install.available no hay boton Instalar y se explica lo que falta', async () => {
    const { api } = installApi({
      pty_list_cli_catalog: vi.fn(async () => makeCatalogResponse([
        notInstalled('gemini', { available: false, code: 'PREREQUISITE_MISSING', missing_requirements: ['npm'] }),
        notInstalled('codex', { available: false, code: 'NO_INSTALL_METHOD', command: null, method: null, verified: false }),
        notInstalled('opencode', { verified: false }),
        makeCli('custom_shell')
      ]))
    });
    renderMatrix(api);
    await screen.findByTestId('cli-row-gemini');
    ['gemini', 'codex', 'opencode'].forEach((id) => expect(screen.queryByTestId(`cli-install-${id}`)).toBeNull());
    expect(screen.getByTestId('cli-install-block-gemini').textContent).toMatch(/Faltan herramientas previas: npm/);
    expect(screen.getByTestId('cli-install-block-codex').textContent).toMatch(/método de instalación verificado/);
    expect(screen.getByTestId('cli-install-block-opencode').textContent).toMatch(/no está verificado/);
    expect(api.pty_prepare_install).not.toHaveBeenCalled();
  });

  it.each([
    [{ success: false, code: 'PREREQUISITE_MISSING', missing: ['npm'], message: 'Faltan herramientas previas para instalar Gemini: npm.' }, /Faltan herramientas previas/, /npm/],
    [{ success: false, code: 'INSTALL_NOT_ALLOWED', message: 'Catálogo del usuario' }, /no se puede instalar desde la interfaz/, /manualmente/],
    [{ success: false, code: 'ALREADY_INSTALLED', message: 'ya instalado' }, /ya está instalado/, /Actualiza el estado/]
  ])('si prepare falla (%o) no hay confirmacion y se explica la causa', async (res, titleRx, hintRx) => {
    const { api } = installApi({ pty_prepare_install: vi.fn(async () => res) });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-install-gemini'));

    const notice = await screen.findByTestId('slider-notice');
    expect(notice.getAttribute('data-code')).toBe(res.code);
    expect(screen.getByTestId('slider-notice-title').textContent).toMatch(titleRx);
    expect(screen.getByTestId('slider-notice-hint').textContent).toMatch(hintRx);
    expect(screen.queryByTestId('install-confirm-gemini')).toBeNull();
    expect(api.pty_install_cli).not.toHaveBeenCalled();
  });

  it('en Windows prepare devuelve el comando para ejecutarlo a mano, sin confirmacion ni token', async () => {
    const { api } = installApi({
      pty_prepare_install: vi.fn(async () => ({
        success: false, degraded: true, code: 'TERMINAL_PLATFORM_UNSUPPORTED',
        manual_command: MANUAL_CMD, install_command: 'winget install Example.Gemini'
      }))
    });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-install-gemini'));
    await screen.findByTestId('slider-notice');
    expect(screen.getByTestId('slider-notice-title').textContent).toMatch(/Windows/);
    expect(screen.getByTestId('slider-notice-manual-cmd').textContent).toBe(MANUAL_CMD);
    expect(screen.getByTestId('slider-notice-install-command').textContent).toBe('winget install Example.Gemini');
    expect(screen.queryByTestId('install-confirm-gemini')).toBeNull();
  });

  it('si el token vence o ya se uso, install lo explica y no abre ningun panel', async () => {
    const { api } = installApi({
      pty_install_cli: vi.fn(async () => ({ success: false, code: 'INSTALL_TOKEN_EXPIRED', message: 'La confirmacion vencio' }))
    });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-install-gemini'));
    fireEvent.click(await screen.findByTestId('install-confirm-btn'));

    await screen.findByTestId('slider-notice');
    expect(screen.getByTestId('slider-notice').getAttribute('data-code')).toBe('INSTALL_TOKEN_EXPIRED');
    expect(screen.getByTestId('slider-notice-hint').textContent).toMatch(/Pulsa «Instalar» otra vez/);
    expect(screen.queryByTestId('terminal-pane-pane_1')).toBeNull();
    expect(screen.queryByTestId('install-confirm-gemini')).toBeNull();
  });

  it('la confirmacion se retira sola al vencer su plazo', async () => {
    const { api } = installApi({
      pty_prepare_install: vi.fn(async () => prepareResponse({ expires_in_s: 0.05 }))
    });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-install-gemini'));
    await screen.findByTestId('install-confirm-gemini');

    await waitFor(() => expect(screen.queryByTestId('install-confirm-gemini')).toBeNull());
    expect(screen.getByTestId('slider-notice').getAttribute('data-code')).toBe('INSTALL_TOKEN_EXPIRED');
    expect(api.pty_install_cli).not.toHaveBeenCalled();
  });

  it('desde CLI_BINARY_NOT_FOUND con install_available, "Revisar instalación" abre la confirmacion; sin ella solo ofrece la ruta', async () => {
    const found = installApi({
      pty_spawn_pane: vi.fn(async () => ({
        success: false, degraded: false, code: 'CLI_BINARY_NOT_FOUND', cli_profile: 'gemini', install_available: true
      }))
    });
    // gemini figura instalado en el catalogo pero el spawn no lo encuentra (carrera): el aviso manda.
    found.api.pty_list_cli_catalog.mockImplementation(async () => makeCatalogResponse([
      makeCli('gemini'), makeCli('custom_shell')
    ]));
    const first = renderMatrix(found.api);
    fireEvent.click(await screen.findByTestId('cli-open-gemini'));
    await screen.findByTestId('slider-notice');
    expect(screen.getByTestId('slider-notice-action-configure_path')).toBeTruthy();
    expect(screen.getByTestId('slider-notice-action-install')).toBeTruthy();
    first.unmount();

    const none = installApi({
      pty_spawn_pane: vi.fn(async () => ({
        success: false, degraded: false, code: 'CLI_BINARY_NOT_FOUND', cli_profile: 'claude', install_available: false
      }))
    });
    renderMatrix(none.api);
    fireEvent.click(await screen.findByTestId('cli-open-claude'));
    await screen.findByTestId('slider-notice');
    expect(screen.queryByTestId('slider-notice-action-install')).toBeNull();
    expect(screen.getByTestId('slider-notice-action-configure_path')).toBeTruthy();
  });

  it('la accion "Revisar instalación" del aviso prepara la instalacion de ese CLI', async () => {
    const { api } = installApi({
      pty_spawn_pane: vi.fn(async () => ({
        success: false, degraded: false, code: 'CLI_BINARY_NOT_FOUND', cli_profile: 'gemini', install_available: true
      }))
    });
    api.pty_list_cli_catalog.mockImplementation(async () => makeCatalogResponse([
      makeCli('gemini'), makeCli('custom_shell')
    ]));
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-open-gemini'));
    fireEvent.click(await screen.findByTestId('slider-notice-action-install'));
    await waitFor(() => expect(api.pty_prepare_install).toHaveBeenCalledWith('gemini'));
    expect(await screen.findByTestId('install-confirm-gemini')).toBeTruthy();
    expect(api.pty_install_cli).not.toHaveBeenCalled();
  });
});

describe('Ruta del binario de cada CLI', () => {
  it('guarda la ruta con pty_set_cli_path y vuelve a leer el catalogo; vaciarla la borra', async () => {
    const { api } = installApi({
      pty_list_cli_catalog: vi.fn(async () => makeCatalogResponse([
        makeCli('claude', { configured_path: '/opt/claude/bin/claude', configured_path_valid: true }),
        makeCli('custom_shell')
      ]))
    });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-path-toggle-claude'));
    const input = screen.getByTestId('cli-path-input-claude');
    expect(input.value).toBe('/opt/claude/bin/claude');

    fireEvent.change(input, { target: { value: '  /usr/local/bin/claude-nuevo  ' } });
    const callsBefore = api.pty_list_cli_catalog.mock.calls.length;
    fireEvent.click(screen.getByTestId('cli-path-save-claude'));
    await waitFor(() => expect(api.pty_set_cli_path).toHaveBeenCalledWith('claude', '/usr/local/bin/claude-nuevo'));
    await waitFor(() => expect(api.pty_list_cli_catalog.mock.calls.length).toBeGreaterThan(callsBefore));

    fireEvent.click(screen.getByTestId('cli-path-clear-claude'));
    await waitFor(() => expect(api.pty_set_cli_path).toHaveBeenCalledWith('claude', ''));
  });

  it('muestra la causa si el backend rechaza la ruta y avisa de una ruta configurada que dejo de valer', async () => {
    const { api } = installApi({
      pty_set_cli_path: vi.fn(async () => ({ success: false, code: 'PATH_NOT_FOUND', message: 'La ruta no es un archivo existente.' })),
      pty_list_cli_catalog: vi.fn(async () => makeCatalogResponse([
        makeCli('claude', { configured_path: '/viejo/claude', configured_path_valid: false }),
        makeCli('custom_shell')
      ]))
    });
    renderMatrix(api);
    expect((await screen.findByTestId('cli-path-invalid-claude')).textContent).toContain('/viejo/claude');

    fireEvent.click(screen.getByTestId('cli-path-toggle-claude'));
    fireEvent.change(screen.getByTestId('cli-path-input-claude'), { target: { value: '/no/existe' } });
    fireEvent.click(screen.getByTestId('cli-path-save-claude'));
    await screen.findByTestId('slider-notice');
    expect(screen.getByTestId('slider-notice').getAttribute('data-code')).toBe('PATH_NOT_FOUND');
    expect(screen.getByTestId('slider-notice-title').textContent).toMatch(/Ruta del binario no válida/);
  });

  it('la accion "Configurar ruta" de un aviso abre el editor del CLI correcto', async () => {
    const { api } = installApi({
      pty_spawn_pane: vi.fn(async () => ({
        success: false, degraded: false, code: 'CLI_BINARY_NOT_FOUND', cli_profile: 'claude', install_available: false
      }))
    });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-open-claude'));
    fireEvent.click(await screen.findByTestId('slider-notice-action-configure_path'));
    expect(screen.getByTestId('cli-path-editor-claude')).toBeTruthy();
  });

  it('la shell no tiene editor de ruta', async () => {
    const { api } = installApi();
    renderMatrix(api);
    await screen.findByTestId('cli-row-custom_shell');
    expect(screen.queryByTestId('cli-path-toggle-custom_shell')).toBeNull();
  });
});

describe('Confirmacion nativa rechazada (CONFIRMATION_DECLINED)', () => {
  const DECLINED = { success: false, degraded: false, code: 'CONFIRMATION_DECLINED', message: 'Rechazaste el diálogo de confirmación.' };

  async function expectDiscreetDeclinedNotice() {
    const notice = await screen.findByTestId('matrix-notice');
    expect(notice.getAttribute('data-code')).toBe('CONFIRMATION_DECLINED');
    expect(notice.textContent).toMatch(/Cancelaste la confirmación/);
    // Es informativo, no un error: ningun panel se abre.
    expect(screen.queryByTestId('terminal-pane-pane_1')).toBeNull();
  }

  it('al rechazar la instalacion se muestra un aviso discreto y no se abre ningun panel', async () => {
    const { api } = installApi({ pty_install_cli: vi.fn(async () => DECLINED) });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-install-gemini'));
    fireEvent.click(await screen.findByTestId('install-confirm-btn'));
    await waitFor(() => expect(api.pty_install_cli).toHaveBeenCalledTimes(1));
    await expectDiscreetDeclinedNotice();
    expect(screen.queryByTestId('install-confirm-gemini')).toBeNull();
  });

  it('al rechazar fijar la ruta de un binario se muestra el aviso', async () => {
    const { api } = installApi({ pty_set_cli_path: vi.fn(async () => DECLINED) });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-path-toggle-claude'));
    fireEvent.change(screen.getByTestId('cli-path-input-claude'), { target: { value: '/opt/claude' } });
    fireEvent.click(screen.getByTestId('cli-path-save-claude'));
    await waitFor(() => expect(api.pty_set_cli_path).toHaveBeenCalled());
    await expectDiscreetDeclinedNotice();
  });

  it('al rechazar un shell con comando propio se muestra el aviso', async () => {
    const { api } = installApi({ pty_spawn_pane: vi.fn(async () => DECLINED) });
    renderMatrix(api);
    fireEvent.click(await screen.findByTestId('cli-open-custom_shell'));
    await waitFor(() => expect(api.pty_spawn_pane).toHaveBeenCalled());
    await expectDiscreetDeclinedNotice();
  });
});
