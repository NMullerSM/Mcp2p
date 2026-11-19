// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import React from 'react';
import { AppConfirmDialog } from './AppConfirmDialog';

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function makeConfirmation(over = {}) {
  return {
    token: 'tok-abc-123',
    action_id: 'worker_mode.enable',
    title: 'Activar el modo trabajador',
    message: 'Línea 1\nLínea 2',
    rememberable: true,
    expires_in_s: 120,
    ...over
  };
}

describe('AppConfirmDialog', () => {
  it('cancel_has_initial_focus and restores focus to trigger when closed', () => {
    function Harness() {
      const [conf, setConf] = React.useState(null);
      return (
        <div>
          <button type="button" data-testid="trigger" onClick={() => setConf(makeConfirmation())}>
            Abrir
          </button>
          <AppConfirmDialog
            confirmation={conf}
            onCancel={() => setConf(null)}
            onConfirm={() => setConf(null)}
          />
        </div>
      );
    }

    render(<Harness />);
    const trigger = screen.getByTestId('trigger');
    trigger.focus();
    expect(document.activeElement).toBe(trigger);

    fireEvent.click(trigger);
    const dialog = screen.getByRole('alertdialog');
    expect(dialog.getAttribute('aria-modal')).toBe('true');
    expect(document.activeElement).toBe(screen.getByTestId('app-confirm-cancel'));

    fireEvent.click(screen.getByTestId('app-confirm-cancel'));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('confirm_armed_after_600ms', async () => {
    vi.useFakeTimers();
    const onConfirm = vi.fn();
    render(
      <AppConfirmDialog
        confirmation={makeConfirmation({ rememberable: true })}
        onCancel={vi.fn()}
        onConfirm={onConfirm}
      />
    );

    const acceptBtn = screen.getByTestId('app-confirm-accept');
    expect(acceptBtn.disabled).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(599);
    });
    expect(acceptBtn.disabled).toBe(true);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(acceptBtn.disabled).toBe(false);

    fireEvent.click(screen.getByTestId('app-confirm-remember'));
    fireEvent.click(acceptBtn);
    expect(onConfirm).toHaveBeenCalledWith(true);
  });

  it('escape_cancels', () => {
    const onCancel = vi.fn();
    render(
      <AppConfirmDialog
        confirmation={makeConfirmation()}
        onCancel={onCancel}
        onConfirm={vi.fn()}
      />
    );
    fireEvent.keyDown(screen.getByTestId('app-confirm-dialog'), { key: 'Escape' });
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it('remember_checkbox_only_when_rememberable', () => {
    const { rerender } = render(
      <AppConfirmDialog
        confirmation={makeConfirmation({ rememberable: false })}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />
    );
    expect(screen.queryByTestId('app-confirm-remember')).toBeNull();

    rerender(
      <AppConfirmDialog
        confirmation={makeConfirmation({ token: 'tok-2', rememberable: true })}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />
    );
    expect(screen.getByTestId('app-confirm-remember')).toBeTruthy();
  });

  it('message_rendered_as_text with white-space pre-wrap', () => {
    const payload = '<script>alert(1)</script>\n<b>no html</b>';
    render(
      <AppConfirmDialog
        confirmation={makeConfirmation({ message: payload })}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />
    );
    const msgEl = screen.getByTestId('app-confirm-message');
    expect(msgEl.textContent).toBe(payload);
    expect(msgEl.querySelector('script')).toBeNull();
    expect(msgEl.querySelector('b')).toBeNull();
    expect(msgEl.style.whiteSpace).toBe('pre-wrap');
    expect(screen.getByTestId('app-confirm-dialog').style.width).toMatch(
      /^min\(480px.*calc\(100vw - 32px\)\)$/
    );
  });

  it('focus_trap wraps Tab and Shift+Tab inside the dialog', async () => {
    vi.useFakeTimers();
    render(
      <AppConfirmDialog
        confirmation={makeConfirmation({ rememberable: false })}
        onCancel={vi.fn()}
        onConfirm={vi.fn()}
      />
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(600);
    });
    const dialog = screen.getByTestId('app-confirm-dialog');
    const closeBtn = screen.getByLabelText('Cerrar diálogo');
    const acceptBtn = screen.getByTestId('app-confirm-accept');

    acceptBtn.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(document.activeElement).toBe(closeBtn);

    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(acceptBtn);
  });
});
