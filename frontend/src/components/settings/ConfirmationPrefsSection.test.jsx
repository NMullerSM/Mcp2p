// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import { ConfirmationPrefsSection } from './ConfirmationPrefsSection';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('ConfirmationPrefsSection', () => {
  it('sin ui_list_confirmation_prefs en el puente no pinta nada', () => {
    const { container } = render(<ConfirmationPrefsSection api={{}} />);
    expect(container.innerHTML).toBe('');
  });

  it('lista acciones recordadas y permite olvidar una o todas', async () => {
    let prefs = [
      {
        action_id: 'auto_relay.enable',
        label_es: 'Activar Auto-Relay en un panel',
        risk: 'low',
        remember: true,
        set_at: '2026-11-19T03:00:00Z'
      },
      {
        action_id: 'delivery.enable_pane',
        label_es: 'Activar entrega directa en un panel',
        risk: 'low',
        remember: true,
        set_at: '2026-11-19T03:05:00Z'
      }
    ];
    const api = {
      ui_list_confirmation_prefs: vi.fn(async () => ({ success: true, prefs })),
      ui_forget_confirmation: vi.fn(async (actionId) => {
        prefs = prefs.filter((p) => p.action_id !== actionId);
        return { success: true, action_id: actionId, removed: true };
      }),
      ui_forget_all_confirmations: vi.fn(async () => {
        const removed_count = prefs.length;
        prefs = [];
        return { success: true, removed_count };
      })
    };

    render(<ConfirmationPrefsSection api={api} />);
    expect(await screen.findByTestId('confirmation-pref-item-auto_relay.enable')).toBeTruthy();
    expect(screen.getByTestId('confirmation-pref-item-delivery.enable_pane')).toBeTruthy();

    fireEvent.click(screen.getByTestId('forget-confirmation-auto_relay.enable'));
    await waitFor(() => expect(api.ui_forget_confirmation).toHaveBeenCalledWith('auto_relay.enable'));
    await waitFor(() => expect(screen.queryByTestId('confirmation-pref-item-auto_relay.enable')).toBeNull());
    expect(screen.getByTestId('confirmation-pref-item-delivery.enable_pane')).toBeTruthy();

    fireEvent.click(screen.getByTestId('forget-all-confirmations'));
    await waitFor(() => expect(api.ui_forget_all_confirmations).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId('confirmation-prefs-empty')).toBeTruthy();
  });

  it('muestra estado vacio y error con reintento', async () => {
    const api = {
      ui_list_confirmation_prefs: vi
        .fn()
        .mockResolvedValueOnce({ success: false, message: 'BD ocupada' })
        .mockResolvedValueOnce({ success: true, prefs: [] })
    };
    render(<ConfirmationPrefsSection api={api} />);
    expect((await screen.findByTestId('confirmation-prefs-error')).textContent).toContain('BD ocupada');

    fireEvent.click(screen.getByText('Reintentar'));
    expect(await screen.findByTestId('confirmation-prefs-empty')).toBeTruthy();
  });
});
