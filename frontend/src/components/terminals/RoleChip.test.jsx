// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup, within } from '@testing-library/react';
import React from 'react';
import { RoleChip } from './RoleChip';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('RoleChip', () => {
  it('shows_role_label_from_matrix_status', () => {
    const { rerender } = render(
      <RoleChip api={{}} pane={{ paneId: 'pane_1', kind: 'cli', role: 'orchestrator' }} />
    );
    expect(screen.getByTestId('role-chip-pane_1').textContent).toContain('Orquestador');

    rerender(<RoleChip api={{}} pane={{ paneId: 'pane_1', kind: 'cli', role: 'implementer' }} />);
    expect(screen.getByTestId('role-chip-pane_1').textContent).toContain('Implementador');

    rerender(<RoleChip api={{}} pane={{ paneId: 'pane_1', kind: 'cli', role: 'mediator' }} />);
    expect(screen.getByTestId('role-chip-pane_1').textContent).toContain('Mediador');

    rerender(<RoleChip api={{}} pane={{ paneId: 'pane_1', kind: 'cli', role: 'peer' }} />);
    expect(screen.getByTestId('role-chip-pane_1').textContent).toContain('Par');

    // Auditor con readonly enforced muestra candado; advisory muestra «solo aviso».
    rerender(
      <RoleChip
        api={{}}
        pane={{ paneId: 'pane_1', kind: 'cli', role: 'auditor', readonly: 'enforced' }}
      />
    );
    expect(screen.getByTestId('role-chip-pane_1').textContent).toContain('Auditor');
    expect(screen.getByTestId('role-chip-lock-pane_1')).toBeTruthy();
    expect(screen.queryByTestId('role-chip-advisory-pane_1')).toBeNull();

    rerender(
      <RoleChip
        api={{}}
        pane={{ paneId: 'pane_1', kind: 'cli', role: 'auditor', readonly: 'advisory' }}
      />
    );
    expect(screen.getByTestId('role-chip-advisory-pane_1').textContent).toBe('solo aviso');
    expect(screen.queryByTestId('role-chip-lock-pane_1')).toBeNull();

    // Los paneles de instalacion no muestran chip de rol.
    const { container } = render(
      <RoleChip api={{}} pane={{ paneId: 'pane_2', kind: 'install', role: 'installer' }} />
    );
    expect(container.innerHTML).toBe('');
  });

  it('opens_popover_and_saves_role_and_mandate', async () => {
    const onParentFocus = vi.fn();
    const onOpenSettings = vi.fn();
    const api = {
      pty_get_pane_role: vi.fn(async () => ({
        success: true,
        pane_id: 'pane_1',
        role: 'peer',
        role_label: 'Par',
        mandate: 'mandato previo',
        readonly: 'none',
        relaunch_recommended: false
      })),
      pty_set_pane_role: vi.fn(async (paneId, role, mandate) => ({
        success: true,
        pane_id: paneId,
        role,
        role_label: 'Auditor',
        mandate: mandate.trim(),
        readonly: 'advisory',
        relaunch_recommended: false
      }))
    };

    render(
      <div onClick={onParentFocus}>
        <RoleChip
          api={api}
          pane={{ paneId: 'pane_1', kind: 'cli', role: 'peer' }}
          onOpenSettings={onOpenSettings}
        />
      </div>
    );

    fireEvent.click(screen.getByTestId('role-chip-pane_1'));
    // El clic en el chip no debe propagarse al contenedor del panel.
    expect(onParentFocus).not.toHaveBeenCalled();

    const dialog = await screen.findByRole('dialog', { name: 'Rol de Terminal 1' });
    expect(api.pty_get_pane_role).toHaveBeenCalledWith('pane_1');
    expect(dialog.style.width).toMatch(/^min\(320px.*calc\(100vw - 32px\)\)$/);

    await waitFor(() =>
      expect(within(dialog).getByTestId('role-mandate-pane_1-textarea').value).toBe('mandato previo')
    );

    // Hay los 5 roles de 2.0.2 y el InfoHint apunta a ayuda-roles.
    const select = within(dialog).getByTestId('role-select-pane_1');
    expect(within(select).getAllByRole('option').map((o) => o.value)).toEqual([
      'orchestrator',
      'implementer',
      'auditor',
      'mediator',
      'peer'
    ]);
    fireEvent.click(within(dialog).getByTestId('role-hint-pane_1'));
    expect(onOpenSettings).toHaveBeenCalledWith('terminals_relay', 'ayuda-roles');

    fireEvent.change(select, { target: { value: 'auditor' } });
    fireEvent.change(within(dialog).getByTestId('role-mandate-pane_1-textarea'), {
      target: { value: '  revisa solo auth  ' }
    });
    fireEvent.click(within(dialog).getByTestId('role-save-btn-pane_1'));

    await waitFor(() =>
      expect(api.pty_set_pane_role).toHaveBeenCalledWith('pane_1', 'auditor', '  revisa solo auth  ')
    );
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(screen.getByTestId('role-chip-pane_1').textContent).toContain('Auditor');
    expect(screen.getByTestId('role-chip-advisory-pane_1').textContent).toBe('solo aviso');
  });

  it('shows_relaunch_offer_only_when_recommended_and_api_present', async () => {
    const apiWithoutRelaunch = {
      pty_get_pane_role: vi.fn(async () => ({
        success: true,
        role: 'peer',
        mandate: '',
        readonly: 'none',
        relaunch_recommended: false
      })),
      pty_set_pane_role: vi.fn(async () => ({
        success: true,
        role: 'orchestrator',
        mandate: 'coordina',
        readonly: 'none',
        relaunch_recommended: true
      }))
    };

    const { rerender } = render(
      <RoleChip api={apiWithoutRelaunch} pane={{ paneId: 'pane_2', kind: 'cli', role: 'peer' }} />
    );

    fireEvent.click(screen.getByTestId('role-chip-pane_2'));
    await screen.findByRole('dialog', { name: 'Rol de Terminal 2' });
    fireEvent.click(screen.getByTestId('role-save-btn-pane_2'));

    await waitFor(() => expect(apiWithoutRelaunch.pty_set_pane_role).toHaveBeenCalled());
    // Sin api.pty_relaunch_pane el boton no aparece y el popover se cierra tras guardar.
    expect(screen.queryByTestId('role-relaunch-btn-pane_2')).toBeNull();

    // Con api.pty_relaunch_pane y relaunch_recommended=true aparece la oferta y al pulsarla relanza.
    const apiWithRelaunch = {
      ...apiWithoutRelaunch,
      pty_relaunch_pane: vi.fn(async () => ({ success: true, pane_id: 'pane_2' }))
    };
    rerender(<RoleChip api={apiWithRelaunch} pane={{ paneId: 'pane_2', kind: 'cli', role: 'peer' }} />);

    fireEvent.click(screen.getByTestId('role-chip-pane_2'));
    await screen.findByRole('dialog', { name: 'Rol de Terminal 2' });
    fireEvent.click(screen.getByTestId('role-save-btn-pane_2'));

    const relaunchBtn = await screen.findByTestId('role-relaunch-btn-pane_2');
    expect(relaunchBtn.textContent).toContain('Reiniciar la terminal para aplicarlo (reanuda la conversación)');
    fireEvent.click(relaunchBtn);

    await waitFor(() => expect(apiWithRelaunch.pty_relaunch_pane).toHaveBeenCalledWith('pane_2'));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
  });

  it('escape_closes_and_focus_returns_to_chip', async () => {
    render(<RoleChip api={{}} pane={{ paneId: 'pane_3', kind: 'cli', role: 'peer' }} />);
    const chip = screen.getByTestId('role-chip-pane_3');

    fireEvent.click(chip);
    const dialog = await screen.findByRole('dialog', { name: 'Rol de Terminal 3' });
    const select = within(dialog).getByTestId('role-select-pane_3');
    expect(document.activeElement).toBe(select);

    // Tab en el ultimo boton envuelve al primero (trampa de foco).
    const saveBtn = within(dialog).getByTestId('role-save-btn-pane_3');
    saveBtn.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(document.activeElement).toBe(within(dialog).getByTestId('role-hint-pane_3'));

    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(document.activeElement).toBe(chip);
  });

  it('error_is_shown_with_cause', async () => {
    const api = {
      pty_get_pane_role: vi.fn(async () => ({
        success: true,
        role: 'peer',
        mandate: '',
        readonly: 'none',
        relaunch_recommended: false
      })),
      pty_set_pane_role: vi.fn(async () => ({
        success: false,
        code: 'PANE_NOT_FOUND',
        message: 'No hay un panel activo'
      }))
    };

    render(<RoleChip api={api} pane={{ paneId: 'pane_1', kind: 'cli', role: 'peer' }} />);
    fireEvent.click(screen.getByTestId('role-chip-pane_1'));
    await screen.findByRole('dialog', { name: 'Rol de Terminal 1' });

    fireEvent.click(screen.getByTestId('role-save-btn-pane_1'));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('No se pudo guardar el rol');
    expect(alert.textContent).toContain('La terminal ya no está abierta');
    // El popover sigue abierto para no perder el borrador del usuario.
    expect(screen.getByRole('dialog', { name: 'Rol de Terminal 1' })).toBeTruthy();
  });
});
