// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import { MatrixTopRegion } from './MatrixTopRegion';

afterEach(() => {
  cleanup();
  vi.resetModules();
  vi.doUnmock('./RestoreSessionBanner');
  vi.doUnmock('../relay/HumanChatPanel');
});

describe('MatrixTopRegion', () => {
  it('renders_slots_in_order_and_nothing_when_stubs_return_null', async () => {
    const api = { dummy: true };
    const onRestored = vi.fn();
    const onOpenSettings = vi.fn();
    const panes = { pane_1: { paneId: 'pane_1' } };
    const catalogById = { claude: { id: 'claude' } };
    const relay = { parked: [] };

    const { unmount } = render(
      <MatrixTopRegion
        api={api}
        projectUuid="proj_1"
        onRestored={onRestored}
        panes={panes}
        catalogById={catalogById}
        relay={relay}
        onOpenSettings={onOpenSettings}
      />
    );
    const emptyRegion = screen.getByTestId('matrix-top-region');
    expect(emptyRegion.children.length).toBe(0);
    expect(emptyRegion.textContent).toBe('');
    unmount();

    vi.resetModules();
    vi.doMock('./RestoreSessionBanner', () => ({
      RestoreSessionBanner: (props) => (
        <div
          data-testid="mock-restore-banner"
          data-project={props.projectUuid}
          data-has-callback={String(props.onRestored === onRestored)}
        />
      )
    }));
    vi.doMock('../relay/HumanChatPanel', () => ({
      HumanChatPanel: (props) => (
        <div
          data-testid="mock-human-chat"
          data-project={props.projectUuid}
          data-has-settings={String(props.onOpenSettings === onOpenSettings)}
        />
      )
    }));

    const { MatrixTopRegion: MockedMatrixTopRegion } = await import('./MatrixTopRegion');
    render(
      <MockedMatrixTopRegion
        api={api}
        projectUuid="proj_1"
        onRestored={onRestored}
        panes={panes}
        catalogById={catalogById}
        relay={relay}
        onOpenSettings={onOpenSettings}
      />
    );

    const region = screen.getByTestId('matrix-top-region');
    expect(region.children.length).toBe(2);
    expect(region.children[0].getAttribute('data-testid')).toBe('mock-restore-banner');
    expect(region.children[1].getAttribute('data-testid')).toBe('mock-human-chat');
    expect(region.children[0].getAttribute('data-has-callback')).toBe('true');
    expect(region.children[1].getAttribute('data-has-settings')).toBe('true');
  });
});
