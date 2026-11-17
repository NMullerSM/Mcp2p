// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import React, { useState } from 'react';
import { UpdateModal, pickUpdateAsset } from './UpdateModal';
import { UpdateNotificationPill } from './UpdateNotificationPill';

const BASE = 'https://github.com/NMullerSM/Mcp2p/releases/download/v2.0.1/';
const assets = [
  { name: 'Mcp2p-Linux-Portable.tar.gz', browser_download_url: `${BASE}Mcp2p-Linux-Portable.tar.gz` },
  { name: 'Mcp2p-Setup-v2.0.1.exe', browser_download_url: `${BASE}Mcp2p-Setup-v2.0.1.exe` },
  { name: 'Mcp2p-Windows-Portable.zip', browser_download_url: `${BASE}Mcp2p-Windows-Portable.zip` },
  { name: 'mcp2p_2.0.1_amd64.deb', browser_download_url: `${BASE}mcp2p_2.0.1_amd64.deb` },
  { name: 'SHA256SUMS', browser_download_url: `${BASE}SHA256SUMS` },
];
const release = { tag_name: 'v2.0.1', html_url: 'https://github.com/NMullerSM/Mcp2p/releases/tag/v2.0.1', body: 'Notas', assets };
const winInstaller = { success: true, platform: 'win32', mode: 'system', kind: 'windows_installer' };
const idle = { status: 'idle', percent: 0, downloaded_mb: 0, total_mb: 0 };

function makeApi(overrides = {}) {
  return {
    get_install_mode: vi.fn(() => Promise.resolve(winInstaller)),
    get_update_progress: vi.fn(() => Promise.resolve(idle)),
    start_auto_update: vi.fn(() => Promise.resolve({ success: true })),
    apply_update_and_restart: vi.fn(() => Promise.resolve({ success: true })),
    open_release_page: vi.fn(() => Promise.resolve({ success: true })),
    get_update_log: vi.fn(() => Promise.resolve({ success: true, path: 'x', text: 'LINEA-DE-LOG' })),
    ...overrides,
  };
}

// Mismo contrato que App.jsx: el estado vive en el padre y el modal lo actualiza con onProgressChange.
function Host({ api, initial = idle, installMode = winInstaller, rel = release }) {
  const [progress, setProgress] = useState(initial);
  return (
    <>
      <UpdateModal release={rel} currentVersion="2.0.0" api={api} installMode={installMode} progress={progress} onProgressChange={setProgress} onClose={() => {}} />
    </>
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('pickUpdateAsset', () => {
  it('elige el asset según cómo está instalada esta copia', () => {
    const name = (m) => pickUpdateAsset(assets, m)?.name;
    expect(name({ kind: 'windows_installer' })).toBe('Mcp2p-Setup-v2.0.1.exe');
    expect(name({ kind: 'windows_portable' })).toBe('Mcp2p-Windows-Portable.zip');
    expect(name({ kind: 'linux_deb' })).toBe('mcp2p_2.0.1_amd64.deb');
    expect(name({ kind: 'linux_portable' })).toBe('Mcp2p-Linux-Portable.tar.gz');
    expect(name({ platform: 'linux', mode: 'portable' })).toBe('Mcp2p-Linux-Portable.tar.gz');
    expect(name(null)).toBe('mcp2p_2.0.1_amd64.deb');
    expect(pickUpdateAsset(assets, null, true).name).toBe('Mcp2p-Setup-v2.0.1.exe');
  });

  it('devuelve undefined si la release no trae un asset apto', () => {
    expect(pickUpdateAsset([{ name: 'SHA256SUMS' }], { kind: 'windows_installer' })).toBeUndefined();
    expect(pickUpdateAsset(undefined, { kind: 'windows_installer' })).toBeUndefined();
  });
});

describe('UpdateModal', () => {
  it('Actualizar muestra progreso al instante y sigue el sondeo hasta "listo" (regresión: antes no pasaba nada)', async () => {
    const seq = [
      { status: 'downloading', percent: 40, downloaded_mb: 20, total_mb: 50 },
      { status: 'verifying', percent: 96, downloaded_mb: 50, total_mb: 50 },
      { status: 'ready_to_restart', percent: 100, downloaded_mb: 50, total_mb: 50 },
    ];
    let i = 0;
    const api = makeApi({
      get_update_progress: vi.fn(() => Promise.resolve(i === 0 && api.start_auto_update.mock.calls.length === 0 ? idle : seq[Math.min(i++, seq.length - 1)])),
    });
    render(<Host api={api} />);
    fireEvent.click(await screen.findByText('Actualizar', { selector: 'button' }));

    expect(api.start_auto_update).toHaveBeenCalledWith(`${BASE}Mcp2p-Setup-v2.0.1.exe`, 'v2.0.1');
    expect(await screen.findByText(/Descargando actualización/)).toBeTruthy();
    expect(await screen.findByText(/40% — 20 MB \/ 50 MB/)).toBeTruthy();
    expect(await screen.findByText(/Verificando integridad/)).toBeTruthy();
    expect(await screen.findByText('Reiniciar para actualizar', { selector: 'button' }, { timeout: 3000 })).toBeTruthy();
  });

  it('la pastilla refleja los estados del actualizador (descargando, verificando, instalando, error)', () => {
    const states = [
      [{ status: 'downloading', percent: 10 }, /Descargando\.\.\. 10%/],
      [{ status: 'verifying', percent: 96 }, /Verificando actualización/],
      [{ status: 'installing', percent: 100 }, /Instalando actualización/],
      [{ status: 'error', error: 'x' }, /Falló la actualización/],
      [{ status: 'ready_to_restart', percent: 100 }, /Actualización lista/],
    ];
    for (const [progress, re] of states) {
      const { unmount } = render(<UpdateNotificationPill release={release} progress={progress} onOpenModal={() => {}} onDismiss={() => {}} />);
      expect(screen.getByRole('status').textContent).toMatch(re);
      unmount();
    }
  });

  it('listo para reiniciar: aplica la actualización y pasa a "instalando" con aviso de reapertura', async () => {
    const api = makeApi({ get_update_progress: vi.fn(() => Promise.resolve({ status: 'ready_to_restart', percent: 100, downloaded_mb: 50, total_mb: 50 })) });
    render(<Host api={api} initial={{ status: 'ready_to_restart', percent: 100, downloaded_mb: 50, total_mb: 50 }} />);
    fireEvent.click(await screen.findByText('Reiniciar para actualizar', { selector: 'button' }));
    expect(api.apply_update_and_restart).toHaveBeenCalled();
    expect(await screen.findByText(/Instalando la actualización/)).toBeTruthy();
    expect(screen.getByText(/se volverá a abrir sola/)).toBeTruthy();
  });

  it('si el instalador no arranca, el error se ve y se puede reintentar', async () => {
    const api = makeApi({
      get_update_progress: vi.fn(() => Promise.resolve({ status: 'ready_to_restart', percent: 100 })),
      apply_update_and_restart: vi.fn(() => Promise.resolve({ success: false, error: 'No se pudo iniciar el instalador (requiere permisos de administrador)' })),
    });
    render(<Host api={api} initial={{ status: 'ready_to_restart', percent: 100 }} />);
    fireEvent.click(await screen.findByText('Reiniciar para actualizar', { selector: 'button' }));
    expect((await screen.findByRole('alert')).textContent).toContain('permisos de administrador');
    expect(screen.getByText('Reiniciar para actualizar', { selector: 'button' }).disabled).toBe(false);
  });

  it('error de descarga: muestra la causa, Reintentar, página de descarga y registro', async () => {
    const api = makeApi({
      get_update_progress: vi.fn(() => Promise.resolve({ status: 'error', percent: 0, error: 'Fallo crítico de integridad: el hash SHA256 no coincide con el manifiesto oficial.' })),
    });
    render(<Host api={api} initial={{ status: 'error', percent: 0, error: 'Fallo crítico de integridad: el hash SHA256 no coincide con el manifiesto oficial.' }} />);

    expect((await screen.findByRole('alert')).textContent).toContain('no coincide');

    fireEvent.click(screen.getByText('Ver registro de actualización'));
    expect(await screen.findByText('LINEA-DE-LOG')).toBeTruthy();
    expect(api.get_update_log).toHaveBeenCalled();

    fireEvent.click(screen.getByText('Abrir página de descarga'));
    await waitFor(() => expect(api.open_release_page).toHaveBeenCalledWith(release.html_url));

    fireEvent.click(screen.getByText('Reintentar'));
    await waitFor(() => expect(api.start_auto_update).toHaveBeenCalledTimes(1));
  });

  it('start_auto_update rechazado (p. ej. URL no permitida) se muestra como error, no se pierde', async () => {
    const api = makeApi({ start_auto_update: vi.fn(() => Promise.resolve({ success: false, error: 'URL no permitida por directiva de seguridad: x' })) });
    render(<Host api={api} />);
    fireEvent.click(await screen.findByText('Actualizar', { selector: 'button' }));
    expect((await screen.findByRole('alert')).textContent).toContain('URL no permitida');
  });

  it('start_auto_update lanza una excepción: se muestra el mensaje', async () => {
    const api = makeApi({ start_auto_update: vi.fn(() => Promise.reject(new Error('canal roto'))) });
    render(<Host api={api} />);
    fireEvent.click(await screen.findByText('Actualizar', { selector: 'button' }));
    expect((await screen.findByRole('alert')).textContent).toContain('canal roto');
  });

  it('sin asset apto: explica el motivo y ofrece la página de descarga por el backend (no window.open)', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const api = makeApi();
    const noExe = { ...release, assets: assets.filter((a) => !a.name.endsWith('.exe')) };
    render(<Host api={api} rel={noExe} />);
    fireEvent.click(await screen.findByText('Actualizar', { selector: 'button' }));

    expect((await screen.findByRole('alert')).textContent).toContain('no incluye un instalador');
    expect(api.start_auto_update).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText('Abrir página de descarga'));
    await waitFor(() => expect(api.open_release_page).toHaveBeenCalledWith(release.html_url));
    expect(openSpy).not.toHaveBeenCalled();
  });

  it('modo portable de Windows descarga el .zip', async () => {
    const api = makeApi({ get_install_mode: vi.fn(() => Promise.resolve({ success: true, platform: 'win32', mode: 'portable', kind: 'windows_portable' })) });
    render(<Host api={api} installMode={null} />);
    expect(await screen.findByText(/paquete portable/)).toBeTruthy();
    fireEvent.click(screen.getByText('Actualizar', { selector: 'button' }));
    await waitFor(() => expect(api.start_auto_update).toHaveBeenCalledWith(`${BASE}Mcp2p-Windows-Portable.zip`, 'v2.0.1'));
  });

  it('si el backend no expone open_release_page, el respaldo no falla en silencio', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null);
    const api = makeApi({ open_release_page: undefined });
    delete api.open_release_page;
    render(<Host api={api} initial={{ status: 'error', error: 'boom' }} />);
    fireEvent.click(await screen.findByText('Abrir página de descarga'));
    expect(openSpy).toHaveBeenCalledWith(release.html_url, '_blank');
  });
});
