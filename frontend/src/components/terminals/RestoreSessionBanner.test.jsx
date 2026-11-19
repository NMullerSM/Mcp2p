// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor, cleanup } from '@testing-library/react';
import React from 'react';
import { RestoreSessionBanner } from './RestoreSessionBanner';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const plan = (items = [{ pane_slot: 'pane_1', cli_id: 'claude', cli_display_name: 'Claude Code', role: 'orchestrator', cwd: '/tmp/p', resume_mode: 'continue', skip: false }], extra = {}) => ({
  project_uuid: 'p1', closed_cleanly: true, items, ...extra
});

function makeApi({ pending = plan(), manual = plan(), policy = 'ask' } = {}) {
  return {
    pty_get_restorable_session: vi.fn(async (_proj, includeHandled) => ({
      success: true,
      policy,
      plan: includeHandled ? manual : pending
    })),
    pty_restore_session: vi.fn(async () => ({ success: true, results: [{ pane_slot: 'pane_1', ok: true, message: 'ok' }] })),
    pty_dismiss_restore: vi.fn(async () => ({ success: true }))
  };
}

describe('RestoreSessionBanner', () => {
  it('offers_the_last_session_and_restores_it', async () => {
    const api = makeApi();
    const onRestored = vi.fn();
    render(<RestoreSessionBanner api={api} projectUuid="p1" onRestored={onRestored} />);

    expect(await screen.findByTestId('restore-session-banner')).toBeTruthy();
    expect(screen.getByTestId('restore-item-pane_1').textContent).toContain('Claude Code');
    expect(screen.getByTestId('restore-item-pane_1').textContent).toContain('Orquestador');

    fireEvent.click(screen.getByTestId('restore-session-confirm'));
    await waitFor(() => expect(api.pty_restore_session).toHaveBeenCalledWith('p1', null, false));
    await waitFor(() => expect(onRestored).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('restore-session-banner')).toBeNull();
  });

  it('dismiss_hides_the_offer_but_keeps_a_manual_button', async () => {
    const api = makeApi();
    render(<RestoreSessionBanner api={api} projectUuid="p1" />);
    fireEvent.click(await screen.findByTestId('restore-session-dismiss'));
    await waitFor(() => expect(api.pty_dismiss_restore).toHaveBeenCalledWith('p1'));
    expect(await screen.findByTestId('restore-session-compact')).toBeTruthy();
    expect(screen.queryByTestId('restore-session-banner')).toBeNull();
  });

  it('manual_restore_after_dismiss_asks_for_the_handled_session', async () => {
    const api = makeApi({ pending: plan([]) });
    render(<RestoreSessionBanner api={api} projectUuid="p1" />);
    const compact = await screen.findByTestId('restore-session-compact');
    fireEvent.click(compact.querySelector('button'));
    fireEvent.click(await screen.findByTestId('restore-session-confirm'));
    await waitFor(() => expect(api.pty_restore_session).toHaveBeenCalledWith('p1', null, true));
  });

  it('auto_policy_restores_once_without_asking', async () => {
    const api = makeApi({ policy: 'auto' });
    const onRestored = vi.fn();
    render(<RestoreSessionBanner api={api} projectUuid="p1" onRestored={onRestored} />);
    await waitFor(() => expect(api.pty_restore_session).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onRestored).toHaveBeenCalledTimes(1));
    expect(await screen.findByTestId('restore-session-feedback')).toBeTruthy();
  });

  it('never_policy_does_not_show_the_banner_nor_restore_alone', async () => {
    const api = makeApi({ policy: 'never' });
    render(<RestoreSessionBanner api={api} projectUuid="p1" />);
    expect(await screen.findByTestId('restore-session-compact')).toBeTruthy();
    expect(screen.queryByTestId('restore-session-banner')).toBeNull();
    expect(api.pty_restore_session).not.toHaveBeenCalled();
  });

  it('renders_nothing_without_a_saved_session_and_explains_skipped_items', async () => {
    const empty = makeApi({ pending: plan([]), manual: plan([]) });
    const { container, unmount } = render(<RestoreSessionBanner api={empty} projectUuid="p1" />);
    await waitFor(() => expect(empty.pty_get_restorable_session).toHaveBeenCalledTimes(2));
    expect(container.textContent).toBe('');
    unmount();

    const mixed = makeApi({
      pending: plan([
        { pane_slot: 'pane_1', cli_id: 'claude', cli_display_name: 'Claude Code', role: 'peer', resume_mode: 'fresh', skip: false },
        { pane_slot: 'pane_2', cli_id: 'codex', role: 'peer', resume_mode: 'fresh', skip: true, warning_details: [{ message: 'Codex ya no está instalado.' }] }
      ])
    });
    render(<RestoreSessionBanner api={mixed} projectUuid="p1" />);
    expect((await screen.findByTestId('restore-skipped')).textContent).toContain('Codex ya no está instalado.');
    expect(screen.getByTestId('restore-session-confirm').textContent).toContain('la terminal');
  });

  it('a_failed_restore_shows_the_error_and_keeps_the_offer', async () => {
    const api = makeApi();
    api.pty_restore_session = vi.fn(async () => ({ success: false, message: 'Sin sitio para más terminales.' }));
    render(<RestoreSessionBanner api={api} projectUuid="p1" />);
    fireEvent.click(await screen.findByTestId('restore-session-confirm'));
    expect((await screen.findByTestId('restore-session-feedback')).textContent).toContain('Sin sitio');
    expect(screen.getByTestId('restore-session-banner')).toBeTruthy();
  });
});
