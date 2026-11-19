// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup, act } from '@testing-library/react';
import React from 'react';
import { useDeliveryActions } from './useDeliveryActions';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function Harness({ api, onChanged }) {
  const { setMode, busy, error, dialog } = useDeliveryActions({ api, projectUuid: 'proj_1', onChanged });
  return React.createElement(
    'div',
    null,
    React.createElement(
      'button',
      { type: 'button', 'data-testid': 'set-direct', disabled: busy, onClick: () => void setMode('pane_1', 'direct') },
      'Directa'
    ),
    error && React.createElement('p', { 'data-testid': 'delivery-error' }, error),
    dialog
  );
}

describe('useDeliveryActions', () => {
  it('confirmation_required_opens_dialog_then_confirms', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = {
      pty_set_delivery_mode: vi.fn(async () => ({
        success: false,
        code: 'CONFIRMATION_REQUIRED',
        token: 'tok-1',
        title: 'Activar entrega directa',
        message: 'MCP2P escribirá mensajes en esta terminal.',
        rememberable: false
      })),
      ui_confirm_action: vi.fn(async () => ({ success: true, code: 'OK', delivery_mode: 'direct' }))
    };
    const onChanged = vi.fn();
    render(React.createElement(Harness, { api, onChanged }));

    fireEvent.click(screen.getByTestId('set-direct'));
    expect(api.pty_set_delivery_mode).toHaveBeenCalledWith('pane_1', 'direct');
    await screen.findByTestId('app-confirm-dialog');
    expect(onChanged).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    fireEvent.click(screen.getByTestId('app-confirm-accept'));
    await waitFor(() => expect(api.ui_confirm_action).toHaveBeenCalledWith('tok-1', true, false));
    await waitFor(() =>
      expect(onChanged).toHaveBeenCalledWith({ kind: 'mode', paneId: 'pane_1', mode: 'direct', projectUuid: 'proj_1' })
    );
    expect(screen.queryByTestId('delivery-error')).toBeNull();
  });

  it('error_message_on_failure', async () => {
    const api = {
      pty_set_delivery_mode: vi.fn(async () => ({ success: false, code: 'INVALID_ARGUMENT', message: 'modo no valido' }))
    };
    const onChanged = vi.fn();
    render(React.createElement(Harness, { api, onChanged }));

    fireEvent.click(screen.getByTestId('set-direct'));
    const error = await screen.findByTestId('delivery-error');
    expect(error.textContent).toContain('No se pudo cambiar la entrega directa');
    expect(error.textContent).toContain('Faltan datos');
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('declined_confirmation_is_not_an_error', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = {
      pty_set_delivery_mode: vi.fn(async () => ({
        success: false,
        code: 'CONFIRMATION_REQUIRED',
        token: 'tok-2',
        title: 'Activar',
        message: 'msg',
        rememberable: false
      })),
      ui_confirm_action: vi.fn(async () => ({ success: true }))
    };
    render(React.createElement(Harness, { api, onChanged: vi.fn() }));
    fireEvent.click(screen.getByTestId('set-direct'));
    await screen.findByTestId('app-confirm-dialog');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    fireEvent.click(screen.getByTestId('app-confirm-cancel'));
    await waitFor(() => expect(api.ui_confirm_action).toHaveBeenCalledWith('tok-2', false, false));
    expect(screen.queryByTestId('delivery-error')).toBeNull();
  });

  it('exception_becomes_spanish_error', async () => {
    const api = { pty_set_delivery_mode: vi.fn(async () => { throw new Error('puente caído'); }) };
    render(React.createElement(Harness, { api, onChanged: vi.fn() }));
    fireEvent.click(screen.getByTestId('set-direct'));
    const error = await screen.findByTestId('delivery-error');
    expect(error.textContent).toContain('puente caído');
  });
});
