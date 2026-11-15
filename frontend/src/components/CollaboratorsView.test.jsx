// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import React from 'react';
import { CollaboratorsView, formatLockRemaining } from './CollaboratorsView';
import { ViewSliderProvider } from './ViewSlider';

const PROJECT = { project_uuid: 'proj_abc123', name: 'Proyecto Demo' };

function makeApi({ collaborators = [] } = {}) {
  return {
    get_project_collaborators: vi.fn(() => Promise.resolve({ success: true, collaborators })),
    generate_p2p_invite: vi.fn(() => Promise.resolve({ success: true, invite_url: 'mcp2p://join?token=xyz' })),
    get_mcp_and_agents_status: vi.fn(() => Promise.resolve({
      success: true,
      mcp_server: { server_name: 'mcp2p-server', active_node_uuid: 'node_owner', tools_count: 10 },
      connected_agents: []
    })),
    approve_collaborator: vi.fn(() => Promise.resolve({ success: true })),
    reject_collaborator: vi.fn(() => Promise.resolve({ success: true })),
    ingest_join_request: vi.fn(() => Promise.resolve({ success: true, status: 'pending_approval' }))
  };
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('formatLockRemaining', () => {
  it('devuelve null cuando no hay locked_until', () => {
    expect(formatLockRemaining(null)).toBeNull();
    expect(formatLockRemaining(undefined)).toBeNull();
    expect(formatLockRemaining('')).toBeNull();
  });

  it('devuelve null cuando la fecha ya pasó (bloqueo expirado)', () => {
    const past = new Date(Date.now() - 60000).toISOString();
    expect(formatLockRemaining(past)).toBeNull();
  });

  it('devuelve null para una fecha inválida', () => {
    expect(formatLockRemaining('not-a-date')).toBeNull();
  });

  it('formatea minutos cuando el bloqueo dura menos de una hora', () => {
    const future = new Date(Date.now() + 29 * 60000).toISOString();
    expect(formatLockRemaining(future)).toBe('29min');
  });

  it('formatea horas y minutos cuando el bloqueo dura una hora o más', () => {
    const future = new Date(Date.now() + (23 * 60 + 40) * 60000).toISOString();
    expect(formatLockRemaining(future)).toBe('23h 40min');
  });

  it('formatea solo horas cuando los minutos sobrantes son cero', () => {
    const future = new Date(Date.now() + 24 * 60 * 60000).toISOString();
    expect(formatLockRemaining(future)).toBe('24h');
  });
});

describe('CollaboratorsView - estado de bloqueo por rate-limit', () => {
  it('muestra Aceptar/Rechazar para una solicitud pendiente sin bloqueo (locked_until null)', async () => {
    const api = makeApi({
      collaborators: [
        { node_uuid: 'node_1', status: 'pending_approval', role: 'collaborator', node_kind: 'human', locked_until: null }
      ]
    });
    render(<CollaboratorsView project={PROJECT} api={api} />);

    await screen.findByText('Pendiente de Aprobación');
    expect(screen.getByTitle('Aprobar acceso a este colaborador')).toBeTruthy();
    expect(screen.getByTitle('Rechazar solicitud')).toBeTruthy();
    expect(screen.queryByTestId('collab-lock-state')).toBeNull();
  });

  it('muestra el badge de bloqueo informativo en una fila RECHAZADA con bloqueo activo (el estado real tras 3+ rechazos)', async () => {
    // Reviewer finding: the state that actually happens after 3+ rejections is a
    // 'rejected' row with an active lock -- record_rejection() flips the row to
    // 'rejected' in the SAME operation that crosses the lock threshold, so
    // 'pending_approval' + locked is not a real reachable state through normal use.
    const future = new Date(Date.now() + 25 * 60000).toISOString();
    const api = makeApi({
      collaborators: [
        { node_uuid: 'node_1', status: 'rejected', role: 'collaborator', node_kind: 'human', rejection_count: 3, locked_until: future }
      ]
    });
    render(<CollaboratorsView project={PROJECT} api={api} />);

    await screen.findByText('Rechazado');
    const lockState = await screen.findByTestId('collab-lock-state');
    expect(lockState.textContent).toContain('Bloqueado');
    expect(lockState.textContent).toContain('25min');

    // Informational only: Remover must stay available, the lock never hides it.
    expect(screen.getByTitle('Remover colaborador del proyecto')).toBeTruthy();
  });

  it('una fila RECHAZADA sin bloqueo activo (1-2 rechazos) no muestra el badge de bloqueo', async () => {
    const api = makeApi({
      collaborators: [
        { node_uuid: 'node_1', status: 'rejected', role: 'collaborator', node_kind: 'human', rejection_count: 1, locked_until: null }
      ]
    });
    render(<CollaboratorsView project={PROJECT} api={api} />);

    await screen.findByText('Rechazado');
    expect(screen.queryByTestId('collab-lock-state')).toBeNull();
  });

  it('un bloqueo en una fila PENDIENTE (caso de carrera) es puramente informativo: Aceptar/Rechazar siguen activos', async () => {
    // The rate limit governs whether the REQUESTER can resubmit, not whether the OWNER
    // can act on a row already in front of them (approve_collaborator has no lock
    // check at all) -- a lock badge here, even in the rare race-condition case where
    // it occurs, must never disable or hide Aprobar/Rechazar.
    const future = new Date(Date.now() + 25 * 60000).toISOString();
    const api = makeApi({
      collaborators: [
        { node_uuid: 'node_1', status: 'pending_approval', role: 'collaborator', node_kind: 'human', rejection_count: 3, locked_until: future }
      ]
    });
    render(<CollaboratorsView project={PROJECT} api={api} />);

    const lockState = await screen.findByTestId('collab-lock-state');
    expect(lockState.textContent).toContain('Bloqueado');
    expect(lockState.textContent).toContain('25min');

    const acceptButton = screen.getByTitle('Aprobar acceso a este colaborador').closest('button');
    const rejectButton = screen.getByTitle('Rechazar solicitud').closest('button');
    expect(acceptButton.disabled).toBeFalsy();
    expect(rejectButton.disabled).toBeFalsy();

    fireEvent.click(acceptButton);
    await waitFor(() => expect(api.approve_collaborator).toHaveBeenCalledWith('proj_abc123', 'node_1'));
  });

  it('no bloquea una solicitud pendiente cuyo locked_until ya expiró', async () => {
    const past = new Date(Date.now() - 5 * 60000).toISOString();
    const api = makeApi({
      collaborators: [
        { node_uuid: 'node_1', status: 'pending_approval', role: 'collaborator', node_kind: 'human', rejection_count: 3, locked_until: past }
      ]
    });
    render(<CollaboratorsView project={PROJECT} api={api} />);

    await screen.findByText('Pendiente de Aprobación');
    expect(screen.queryByTestId('collab-lock-state')).toBeNull();
    expect(screen.getByTitle('Aprobar acceso a este colaborador')).toBeTruthy();
  });

  it('llama a approve_collaborator al pulsar Aceptar en una fila no bloqueada', async () => {
    const api = makeApi({
      collaborators: [
        { node_uuid: 'node_1', status: 'pending_approval', role: 'collaborator', node_kind: 'human', locked_until: null }
      ]
    });
    render(<CollaboratorsView project={PROJECT} api={api} />);

    const acceptButton = await screen.findByTitle('Aprobar acceso a este colaborador');
    fireEvent.click(acceptButton);
    await waitFor(() => expect(api.approve_collaborator).toHaveBeenCalledWith('proj_abc123', 'node_1'));
  });

  it('un colaborador aprobado (no pendiente) nunca muestra el estado de bloqueo aunque tenga locked_until', async () => {
    const future = new Date(Date.now() + 60 * 60000).toISOString();
    const api = makeApi({
      collaborators: [
        { node_uuid: 'node_1', status: 'approved', role: 'collaborator', node_kind: 'human', locked_until: future }
      ]
    });
    render(<CollaboratorsView project={PROJECT} api={api} />);

    await screen.findByText('Aprobado');
    expect(screen.queryByTestId('collab-lock-state')).toBeNull();
  });
});

describe('CollaboratorsView - pegar código de solicitud (ingesta manual, nivel 2)', () => {
  const OWNER_ROW = { node_uuid: 'node_owner', status: 'approved', role: 'owner', node_kind: 'human', locked_until: null };
  const PENDING_ROW = { node_uuid: 'node_req', github_username: 'requester', status: 'pending_approval', role: 'collaborator', node_kind: 'human', locked_until: null };

  async function renderAndFill(api, code) {
    render(
      <ViewSliderProvider viewKey="collaborators">
        <CollaboratorsView project={PROJECT} api={api} />
      </ViewSliderProvider>
    );
    // Ingesta manual vive en el slider lateral derecho: se abre con la pestana del borde.
    fireEvent.click(await screen.findByTestId('view-slider-tab'));
    const input = await screen.findByLabelText('Código de solicitud de unión');
    fireEvent.change(input, { target: { value: code } });
    return input;
  }

  it('llama a ingest_join_request con el código y el project_uuid, y refresca la lista mostrando la nueva fila pendiente', async () => {
    const api = makeApi({ collaborators: [OWNER_ROW] });
    api.get_project_collaborators
      .mockResolvedValueOnce({ success: true, collaborators: [OWNER_ROW] })
      .mockResolvedValue({ success: true, collaborators: [OWNER_ROW, PENDING_ROW] });

    const input = await renderAndFill(api, '  req_code_abc  ');
    expect(screen.queryByText('Pendiente de Aprobación')).toBeNull();

    fireEvent.click(screen.getByText('Registrar Solicitud'));

    await waitFor(() => expect(api.ingest_join_request).toHaveBeenCalledWith('req_code_abc', 'proj_abc123'));
    expect(await screen.findByText('Pendiente de Aprobación')).toBeTruthy();
    expect(screen.getByTitle('Aprobar acceso a este colaborador')).toBeTruthy();
    expect(api.get_project_collaborators).toHaveBeenCalledTimes(2);
    expect(input.value).toBe('');
    const msg = screen.getByTestId('ingest-request-code-message');
    expect(msg.textContent).toContain('Solicitud registrada');
  });

  it('muestra el estado de bloqueo cuando el backend responde locked', async () => {
    const api = makeApi({ collaborators: [OWNER_ROW] });
    const future = new Date(Date.now() + 29 * 60000).toISOString();
    api.ingest_join_request = vi.fn(() => Promise.resolve({ success: false, status: 'locked', error: 'bloqueada', locked_until: future }));

    const input = await renderAndFill(api, 'req_code_locked');
    fireEvent.click(screen.getByText('Registrar Solicitud'));

    const msg = await screen.findByTestId('ingest-request-code-message');
    expect(msg.textContent).toContain('bloqueada temporalmente');
    expect(msg.textContent).toContain('29min');
    // No se limpia el campo ni se registra ninguna fila nueva.
    expect(input.value).toBe('req_code_locked');
    expect(screen.queryByText('Pendiente de Aprobación')).toBeNull();
  });

  it('muestra el error del backend (p. ej. código de otro proyecto) sin refrescar como éxito', async () => {
    const api = makeApi({ collaborators: [OWNER_ROW] });
    api.ingest_join_request = vi.fn(() => Promise.resolve({
      success: false,
      error: 'El código de solicitud corresponde a un proyecto distinto a este'
    }));

    await renderAndFill(api, 'req_code_other_project');
    fireEvent.click(screen.getByText('Registrar Solicitud'));

    const msg = await screen.findByTestId('ingest-request-code-message');
    expect(msg.textContent).toBe('El código de solicitud corresponde a un proyecto distinto a este');
    expect(api.get_project_collaborators).toHaveBeenCalledTimes(1);
  });

  it('muestra un error de comunicación si la llamada lanza', async () => {
    const api = makeApi({ collaborators: [OWNER_ROW] });
    api.ingest_join_request = vi.fn(() => Promise.reject(new Error('bridge caído')));

    await renderAndFill(api, 'req_code_x');
    fireEvent.click(screen.getByText('Registrar Solicitud'));

    const msg = await screen.findByTestId('ingest-request-code-message');
    expect(msg.textContent).toContain('bridge caído');
  });

  it('no llama a ingest_join_request con el campo vacío (botón deshabilitado)', async () => {
    const api = makeApi({ collaborators: [OWNER_ROW] });
    await renderAndFill(api, '   ');
    const button = screen.getByText('Registrar Solicitud').closest('button');
    expect(button.disabled).toBe(true);
    fireEvent.submit(screen.getByTestId('ingest-request-code-form'));
    expect(api.ingest_join_request).not.toHaveBeenCalled();
  });
});
