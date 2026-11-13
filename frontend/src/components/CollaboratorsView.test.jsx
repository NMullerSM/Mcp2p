// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import React from 'react';
import { CollaboratorsView, formatLockRemaining } from './CollaboratorsView';

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
    reject_collaborator: vi.fn(() => Promise.resolve({ success: true }))
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

  it('muestra el badge de bloqueo con cuenta atrás y deshabilita Aceptar cuando locked_until está en el futuro', async () => {
    const future = new Date(Date.now() + 25 * 60000).toISOString();
    const api = makeApi({
      collaborators: [
        { node_uuid: 'node_1', status: 'pending_approval', role: 'collaborator', node_kind: 'human', rejection_count: 3, locked_until: future }
      ]
    });
    render(<CollaboratorsView project={PROJECT} api={api} />);

    const lockState = await screen.findByTestId('collab-lock-state');
    expect(lockState.textContent).toContain('Bloqueado por exceso de rechazos');
    expect(lockState.textContent).toContain('25min');

    // No debe mostrar los botones activos de Aprobar/Rechazar en este estado bloqueado.
    expect(screen.queryByTitle('Aprobar acceso a este colaborador')).toBeNull();
    expect(screen.queryByTitle('Rechazar solicitud')).toBeNull();

    const acceptButton = screen.getByText('Aceptar').closest('button');
    expect(acceptButton.disabled).toBe(true);
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
