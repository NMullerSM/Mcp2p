// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen, waitFor, cleanup } from '@testing-library/react';
import React from 'react';
import App from './App';

const PROJECT_STUB = {
  project_uuid: 'proj_pending1',
  name: 'Proyecto Pendiente',
  join_status: 'pending_approval'
};

function makeApi(overrides = {}) {
  return {
    get_install_mode: vi.fn(async () => ({ success: true })),
    list_projects: vi.fn(async () => ({ success: true, projects: [] })),
    get_github_status: vi.fn(async () => ({ success: true, github: null })),
    set_ui_theme: vi.fn(async () => ({ success: true })),
    join_p2p_project: vi.fn(async () => ({
      success: true,
      status: 'pending_approval',
      project: PROJECT_STUB,
      request_code: 'req_code_xyz'
    })),
    check_join_request_status: vi.fn(async () => ({ success: false, status: 'still_pending', error: 'pendiente' })),
    select_project: vi.fn(async () => ({ success: true })),
    ...overrides
  };
}

beforeEach(() => {
  // App.jsx dispara un fetch a la API de GitHub al montar (chequeo de auto-actualización,
  // envuelto en try/catch) -- lo anulamos para no depender de la red en los tests.
  vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new Error('no network in tests'))));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete window.pywebview;
});

async function renderReadyApp(apiOverrides = {}) {
  const api = makeApi(apiOverrides);
  window.pywebview = { api };
  const utils = render(<App />);
  // Esperar a que usePyWebView detecte window.pywebview.api y la app termine de
  // cargar proyectos/estado de GitHub (el botón "Unirse vía P2P" solo aparece una
  // vez montado el ProjectsHub real, tras el primer ciclo de efectos).
  await screen.findByText('Unirse vía P2P');
  return { ...utils, api };
}

describe('App - flujo de unión P2P pendiente', () => {
  it('muestra la pantalla JoinRequestPending tras un join_p2p_project con status pending_approval', async () => {
    const { api } = await renderReadyApp();

    fireEvent.click(screen.getByText('Unirse vía P2P'));
    const textarea = await screen.findByPlaceholderText(/mcp2p:\/\/join/);
    fireEvent.change(textarea, { target: { value: 'mcp2p://join?token=abc' } });
    fireEvent.click(screen.getByText('Conectar y Desplegar'));

    await waitFor(() => expect(api.join_p2p_project).toHaveBeenCalledWith('mcp2p://join?token=abc'));
    expect(await screen.findByTestId('join-request-pending')).toBeTruthy();
    expect(screen.getByText('Proyecto Pendiente')).toBeTruthy();
    // El modal de unión se cerró y el hub de proyectos ya no está en el DOM.
    expect(screen.queryByText('Unirse vía P2P')).toBeNull();
  });

  it('el botón "Volver a Proyectos" limpia pendingJoinRequest y vuelve al hub de proyectos', async () => {
    await renderReadyApp();

    fireEvent.click(screen.getByText('Unirse vía P2P'));
    const textarea = await screen.findByPlaceholderText(/mcp2p:\/\/join/);
    fireEvent.change(textarea, { target: { value: 'mcp2p://join?token=abc' } });
    fireEvent.click(screen.getByText('Conectar y Desplegar'));

    await screen.findByTestId('join-request-pending');

    fireEvent.click(screen.getByText('Volver a Proyectos'));

    // La pantalla de espera desaparece y el usuario vuelve a ver el hub de proyectos
    // (sin quedar atrapado: esta era la brecha de UX señalada en la revisión).
    await waitFor(() => expect(screen.queryByTestId('join-request-pending')).toBeNull());
    expect(await screen.findByText('Unirse vía P2P')).toBeTruthy();
  });
});
