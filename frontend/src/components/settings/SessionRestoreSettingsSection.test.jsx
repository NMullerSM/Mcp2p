// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import React from 'react';
import { SessionRestoreSettingsSection } from './SessionRestoreSettingsSection';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('SessionRestoreSettingsSection', () => {
  it('loads_and_saves_the_restore_policy', async () => {
    const api = {
      pty_get_restore_policy: vi.fn(async () => ({ success: true, policy: 'auto' })),
      pty_set_restore_policy: vi.fn(async (p) => ({ success: true, policy: p }))
    };
    render(<SessionRestoreSettingsSection api={api} />);
    const select = await screen.findByTestId('session-restore-policy');
    await waitFor(() => expect(select.value).toBe('auto'));
    fireEvent.change(select, { target: { value: 'never' } });
    await waitFor(() => expect(api.pty_set_restore_policy).toHaveBeenCalledWith('never'));
    expect((await screen.findByTestId('session-restore-feedback')).textContent).toContain('guardada');
  });

  it('reverts_the_choice_when_saving_fails', async () => {
    const api = {
      pty_get_restore_policy: vi.fn(async () => ({ success: true, policy: 'ask' })),
      pty_set_restore_policy: vi.fn(async () => ({ success: false, message: 'No se pudo.' }))
    };
    render(<SessionRestoreSettingsSection api={api} />);
    const select = await screen.findByTestId('session-restore-policy');
    await waitFor(() => expect(select.disabled).toBe(false));
    fireEvent.change(select, { target: { value: 'never' } });
    await waitFor(() => expect(select.value).toBe('ask'));
  });

  it('renders_nothing_without_the_backend_method', () => {
    const { container } = render(<SessionRestoreSettingsSection api={{}} />);
    expect(container.textContent).toBe('');
  });
});
