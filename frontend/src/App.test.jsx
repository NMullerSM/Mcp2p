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

  it('el mensaje de "todavía pendiente" admite que un rechazo no se notifica (hallazgo I2)', async () => {
    await renderReadyApp();
    fireEvent.click(screen.getByText('Unirse vía P2P'));
    const textarea = await screen.findByPlaceholderText(/mcp2p:\/\/join/);
    fireEvent.change(textarea, { target: { value: 'mcp2p://join?token=abc' } });
    fireEvent.click(screen.getByText('Conectar y Desplegar'));
    await screen.findByTestId('join-request-pending');

    fireEvent.click(screen.getByText('Reintentar'));

    const msg = await screen.findByTestId('join-request-status-message');
    expect(msg.textContent).toContain('Si la rechazó, la aplicación no puede saberlo');
  });

  it('Escape en la pantalla de espera vuelve al hub y refresca la lista de proyectos', async () => {
    const { api } = await renderReadyApp();
    fireEvent.click(screen.getByText('Unirse vía P2P'));
    const textarea = await screen.findByPlaceholderText(/mcp2p:\/\/join/);
    fireEvent.change(textarea, { target: { value: 'mcp2p://join?token=abc' } });
    fireEvent.click(screen.getByText('Conectar y Desplegar'));
    await screen.findByTestId('join-request-pending');
    const callsBefore = api.list_projects.mock.calls.length;

    fireEvent.keyDown(window, { key: 'Escape' });

    await waitFor(() => expect(screen.queryByTestId('join-request-pending')).toBeNull());
    await waitFor(() => expect(api.list_projects.mock.calls.length).toBeGreaterThan(callsBefore));
  });
});

describe('App - stubs de unión pendientes en el hub de proyectos (hallazgo C2)', () => {
  const NORMAL_PROJECT = {
    project_uuid: 'proj_normal1',
    name: 'Proyecto Normal',
    join_status: 'active',
    created_at: '2026-11-01T00:00:00+00:00'
  };
  const STUB_IN_LIST = { ...PROJECT_STUB, created_at: '2026-11-12T00:00:00+00:00' };

  it('un stub pendiente se distingue en el hub y abre JoinRequestPending (no un espacio de trabajo normal)', async () => {
    const { api } = await renderReadyApp({
      list_projects: vi.fn(async () => ({ success: true, projects: [STUB_IN_LIST, NORMAL_PROJECT] })),
      get_pending_join_request_code: vi.fn(async () => ({ success: true, request_code: 'regenerated_req_code' }))
    });

    const pendingCard = await screen.findByTestId('project-card-pending');
    expect(pendingCard.textContent).toContain('Unión pendiente de aprobación');
    expect(pendingCard.textContent).toContain('Ver Solicitud');
    expect(pendingCard.textContent).not.toContain('Abrir Espacio');

    fireEvent.click(screen.getByText('Ver Solicitud'));

    expect(await screen.findByTestId('join-request-pending')).toBeTruthy();
    expect(screen.getByText('regenerated_req_code')).toBeTruthy();
    expect(api.get_pending_join_request_code).toHaveBeenCalledWith('proj_pending1');
    // Nunca se selecciona el stub como proyecto activo del backend.
    expect(api.select_project).not.toHaveBeenCalled();
  });

  it('el doble clic sobre la tarjeta del stub también abre la pantalla de espera', async () => {
    const { api } = await renderReadyApp({
      list_projects: vi.fn(async () => ({ success: true, projects: [STUB_IN_LIST] })),
      get_pending_join_request_code: vi.fn(async () => ({ success: true, request_code: 'regenerated_req_code' }))
    });

    fireEvent.doubleClick(await screen.findByTestId('project-card-pending'));

    expect(await screen.findByTestId('join-request-pending')).toBeTruthy();
    expect(api.select_project).not.toHaveBeenCalled();
  });

  it('la pantalla de espera sigue abriéndose si no se pudo regenerar el código', async () => {
    const { api } = await renderReadyApp({
      list_projects: vi.fn(async () => ({ success: true, projects: [STUB_IN_LIST] })),
      get_pending_join_request_code: vi.fn(async () => { throw new Error('bridge caído'); })
    });

    fireEvent.click(await screen.findByText('Ver Solicitud'));

    expect(await screen.findByTestId('join-request-pending')).toBeTruthy();
    expect(api.select_project).not.toHaveBeenCalled();
  });

  it('un proyecto normal sigue abriéndose con select_project, no con la pantalla de espera', async () => {
    const { api } = await renderReadyApp({
      list_projects: vi.fn(async () => ({ success: true, projects: [NORMAL_PROJECT] })),
      // Si select_project fallara, App no abre el espacio de trabajo: lo usamos para
      // observar la decisión de enrutado sin montar todo ProjectWorkspace.
      select_project: vi.fn(async () => ({ success: false, error: 'detenido en el test' })),
      get_pending_join_request_code: vi.fn(async () => ({ success: true, request_code: 'x' }))
    });
    const alertSpy = vi.spyOn(window, 'alert').mockImplementation(() => {});

    expect((await screen.findByTestId('project-card')).textContent).toContain('Abrir Espacio');
    fireEvent.click(screen.getByText('Abrir Espacio'));

    await waitFor(() => expect(api.select_project).toHaveBeenCalledWith('proj_normal1'));
    expect(api.get_pending_join_request_code).not.toHaveBeenCalled();
    expect(screen.queryByTestId('join-request-pending')).toBeNull();
    await waitFor(() => expect(alertSpy).toHaveBeenCalled());
    // Sigue en el hub: no abrió un espacio de trabajo tras un select_project fallido.
    expect(screen.getByText('Unirse vía P2P')).toBeTruthy();
  });
});
