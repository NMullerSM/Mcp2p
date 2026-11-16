// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, fireEvent, screen, waitFor, cleanup, within } from '@testing-library/react';
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
  window.localStorage.clear();
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

describe('App - Header Combobox, Gobernanza OKF, Filtros Kanban y Vista Dual (Hitos 4.1-4.3)', () => {
  const PROJ_A = {
    project_uuid: 'proj_alpha',
    name: 'Proyecto Alpha',
    join_status: 'active',
    created_at: '2026-11-10T00:00:00+00:00'
  };
  const PROJ_B = {
    project_uuid: 'proj_beta',
    name: 'Proyecto Beta',
    join_status: 'active',
    created_at: '2026-11-11T00:00:00+00:00'
  };

  function makeWorkspaceApiOverrides() {
    return {
      list_projects: vi.fn(async () => ({ success: true, projects: [PROJ_A, PROJ_B] })),
      select_project: vi.fn(async () => ({ success: true })),
      get_system_info: vi.fn(async () => ({ success: true, active_node: 'node_owner' })),
      get_kanban_board: vi.fn(async () => ({
        success: true,
        cards: [
          {
            card_id: 'CARD-1',
            title: 'Implementar Adaptador Hexagonal',
            description: 'Puerto de dominio limpio',
            status: 'in_progress',
            assignee_node_uuid: 'node_alpha_1234',
            branch_ref: 'feat/hex-core',
            tags: ['hexagonal', 'backend', 'okf']
          },
          {
            card_id: 'CARD-2',
            title: 'Auditar Firma Ed25519',
            description: 'Verificación de llaves',
            status: 'backlog',
            assignee_node_uuid: 'node_beta_9999',
            branch_ref: 'fix/ed25519',
            tags: ['seguridad']
          }
        ]
      })),
      subscribe_project_events: vi.fn(async () => ({ success: true })),
      poll_project_events: vi.fn(async () => ({ success: true, events: [] })),
      get_project_collaborators: vi.fn(async () => ({ success: true, collaborators: [] })),
      generate_p2p_invite: vi.fn(async () => ({ success: true, invite_url: '' })),
      get_mcp_and_agents_status: vi.fn(async () => ({ success: true, mcp_server: null, connected_agents: [] })),
      get_okf_tags_index: vi.fn(async () => ({
        success: true,
        tags: { hexagonal: ['docs/okf/adr/ADR-001.md'], seguridad: ['docs/okf/backend/auth.md'] },
        items: [
          { path: 'docs/okf/adr/ADR-001.md', tags: ['hexagonal'] },
          { path: 'docs/okf/backend/auth.md', tags: ['seguridad'] }
        ]
      })),
      get_okf_overview: vi.fn(async () => ({
        success: true,
        adrs: [
          {
            id: 'ADR-001',
            title: 'Arquitectura Hexagonal',
            context: 'Separar dominio de adaptadores',
            decision: 'Puertos y adaptadores',
            consequences: 'Testabilidad'
          }
        ],
        skills: [
          { slug: 'auth', area: 'backend', title: 'Seguridad Ed25519' }
        ],
        validation: { valid: true, stats: { adrs_found: 1, user_stories_found: 2, backend_features: 1 }, errors: [] }
      })),
      get_okf_freshness_report: vi.fn(async () => ({
        success: true,
        summary: { total: 2, fresh: 1, aging: 1, stale: 0, avg_score: 0.85, verified_signatures: 1, unverified_adrs: 0 },
        items: [
          { path: 'docs/okf/adr/ADR-001.md', id: 'ADR-001', title: 'Arquitectura Hexagonal', score: 0.95, status: 'FRESH', signature_valid: true, commits_after: 1, days_old: 2, tags: ['hexagonal'] },
          { path: 'docs/okf/backend/auth.md', id: 'auth', title: 'Seguridad Ed25519', score: 0.72, status: 'AGING', signature_valid: false, commits_after: 4, days_old: 15, tags: ['seguridad'] }
        ]
      })),
      list_okf_folders: vi.fn(async () => ({
        success: true,
        folders: [
          { path: 'docs/okf/adr', category: 'adr', description: 'Decisiones', file_count: 1, required: true }
        ]
      }))
    };
  }

  it('reemplaza el combobox por flecha minimalista al Hub, oculta usuario GitHub en header, mueve Nueva Tarea a Kanban y personaliza tabs', async () => {
    const { api } = await renderReadyApp({
      ...makeWorkspaceApiOverrides(),
      get_github_status: vi.fn(async () => ({
        success: true,
        github: { authenticated: true, username: 'octocat', provider: 'pat', details: 'PAT OK' }
      }))
    });

    // No existe project-switcher-combobox ni @octocat en la barra superior
    expect(screen.queryByTestId('project-switcher-combobox')).toBeNull();
    const ghHeaderBtn = await screen.findByTestId('github-header-btn');
    expect(screen.getByTestId('github-status-dot')).toBeTruthy();
    expect(ghHeaderBtn.textContent).not.toContain('@octocat');

    // Abrir Proyecto Alpha desde el Hub
    const openBtns = await screen.findAllByText('Abrir Espacio');
    fireEvent.click(openBtns[0]);
    await waitFor(() => expect(api.select_project).toHaveBeenCalledWith('proj_alpha'));

    expect(await screen.findByTestId('back-to-hub-btn')).toBeTruthy();
    expect(screen.getByTestId('active-project-name').textContent).toBe('Proyecto Alpha');
    expect(await screen.findByTestId('workspace-toolbar')).toBeTruthy();

    // Nueva Tarea ya no esta en la barra: vive en el slider lateral derecho de Kanban
    // (se abre con la pestana del borde) y desaparece al cambiar a Gobernanza OKF
    expect(within(screen.getByTestId('workspace-toolbar')).queryByTestId('kanban-new-task-btn')).toBeNull();
    fireEvent.click(screen.getByTestId('view-slider-tab'));
    expect(screen.getByTestId('kanban-new-task-btn')).toBeTruthy();
    fireEvent.click(screen.getByTestId('tab-okf'));
    await waitFor(() => expect(screen.queryByTestId('kanban-new-task-btn')).toBeNull());
    expect(screen.getByTestId('workspace-search-input').getAttribute('placeholder')).toContain('Gobernanza OKF');

    // Personalizar barra superior: ocultar Control de Cambios y verificar persistencia
    fireEvent.click(screen.getByTestId('customize-navbar-btn'));
    expect(await screen.findByTestId('navbar-customizer-popover')).toBeTruthy();
    fireEvent.click(screen.getByTestId('navbar-toggle-changes'));
    expect(screen.queryByTestId('tab-changes')).toBeNull();
    fireEvent.click(screen.getByTestId('navbar-toggle-changes'));
    expect(screen.getByTestId('tab-changes')).toBeTruthy();

    // Volver al Hub usando la flecha minimalista
    fireEvent.click(screen.getByTestId('back-to-hub-btn'));
    expect(await screen.findByText('Unirse vía P2P')).toBeTruthy();
  });

  it('aplica Ley de Hick (máx 2 tags + N), filtra por búsqueda/tag y navega a Gobernanza OKF con Freshness', async () => {
    await renderReadyApp(makeWorkspaceApiOverrides());

    const openBtns = await screen.findAllByText('Abrir Espacio');
    fireEvent.click(openBtns[0]);
    await screen.findByTestId('kanban-card-CARD-1');

    // Ley de Hick: CARD-1 tiene 3 tags ['hexagonal', 'backend', 'okf'], solo 2 visibles + '+1'
    expect(screen.getByTestId('card-extra-tags-CARD-1').textContent).toBe('+1');

    // Filtrar por Tag OKF (el filtro vive en el slider lateral de Kanban)
    expect(within(screen.getByTestId('workspace-toolbar')).queryByTestId('workspace-tag-filter')).toBeNull();
    fireEvent.click(screen.getByTestId('view-slider-tab'));
    fireEvent.change(screen.getByTestId('workspace-tag-filter'), { target: { value: 'seguridad' } });
    expect(screen.queryByTestId('kanban-card-CARD-1')).toBeNull();
    expect(screen.getByTestId('kanban-card-CARD-2')).toBeTruthy();

    // Limpiar filtro y navegar directamente a Gobernanza OKF (el patrón de Vista Dual
    // / Split fue retirado: el contenido secundario de cada vista ahora vive en un
    // drawer lateral propio por vista, no en un panel dividido permanente)
    fireEvent.change(screen.getByTestId('workspace-tag-filter'), { target: { value: 'all' } });
    expect(screen.getByTestId('kanban-card-CARD-1')).toBeTruthy();
    fireEvent.keyDown(document, { key: 'Escape' });

    fireEvent.click(screen.getByTestId('tab-okf'));

    // En Gobernanza OKF se ve Freshness y Ed25519
    expect(await screen.findByText(/FRESH · 95%/)).toBeTruthy();
    expect(screen.getByTestId('ed25519-verified-badge')).toBeTruthy();
    expect(screen.getByTestId('okf-active-folders-summary').textContent).toContain('docs/okf/adr (1)');

    // Filtrar ADRs por tag YAML dentro de OKFGovernanceView
    fireEvent.change(screen.getByTestId('okf-tag-filter'), { target: { value: 'seguridad' } });
    expect(screen.queryByTestId('adr-card-ADR-001')).toBeNull();
  });

  it('todas las vistas tienen la pestana del slider lateral y ninguna conserva el boton de apertura antiguo', async () => {
    await renderReadyApp(makeWorkspaceApiOverrides());
    fireEvent.click((await screen.findAllByText('Abrir Espacio'))[0]);
    await screen.findByTestId('kanban-card-CARD-1');

    const OLD_OPEN_BUTTONS = [
      'collab-open-secondary-drawer',
      'branch-open-secondary-drawer',
      'pr-open-details-drawer'
    ];
    const SLIDER_TITLE_BY_TAB = {
      kanban: 'Herramientas del Tablero',
      changes: 'Más acciones & Remoto Git',
      approvals: 'Detalle de GitHub & Historial',
      collaborators: 'Invitar colaboradores & Pasarela MCP',
      okf: 'Gobernanza OKF: filtros y salud',
      terminals: 'Terminales'
    };

    for (const [tab, title] of Object.entries(SLIDER_TITLE_BY_TAB)) {
      fireEvent.click(screen.getByTestId(`tab-${tab}`));
      const sliderTab = await screen.findByTestId('view-slider-tab');
      await waitFor(() => expect(screen.getByTestId('view-slider-panel').getAttribute('aria-label')).toBe(title));
      expect(sliderTab.getAttribute('aria-expanded')).toBe('false');
      for (const id of OLD_OPEN_BUTTONS) expect(screen.queryByTestId(id)).toBeNull();
      // la pestana abre el panel de la vista y Esc lo cierra
      fireEvent.click(sliderTab);
      expect(sliderTab.getAttribute('aria-expanded')).toBe('true');
      fireEvent.keyDown(document, { key: 'Escape' });
      expect(sliderTab.getAttribute('aria-expanded')).toBe('false');
    }
  });
});

describe('App - atajos globales y terminales', () => {
  it('con el foco dentro de una terminal (xterm) las teclas son del proceso: Ctrl+D no cambia el tema', async () => {
    await renderReadyApp();
    const theme = () => document.documentElement.getAttribute('data-theme');
    const before = theme();

    // Un elemento con la clase de xterm: lo que tiene el foco cuando se teclea en una terminal.
    const host = document.createElement('div');
    host.className = 'xterm';
    const helper = document.createElement('textarea');
    host.appendChild(helper);
    document.body.appendChild(host);
    try {
      fireEvent.keyDown(helper, { key: 'd', ctrlKey: true });
      expect(theme()).toBe(before);

      // Fuera de la terminal el atajo de la aplicacion sigue funcionando.
      fireEvent.keyDown(document.body, { key: 'd', ctrlKey: true });
      await waitFor(() => expect(theme()).not.toBe(before));
    } finally {
      document.body.removeChild(host);
    }
  });
});
