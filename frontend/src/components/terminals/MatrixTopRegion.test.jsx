// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import { MatrixTopRegion } from './MatrixTopRegion';

afterEach(() => {
  cleanup();
  vi.resetModules();
  vi.doUnmock('./RestoreSessionBanner');
});

describe('MatrixTopRegion', () => {
  it('renders_the_banner_slot_and_nothing_when_the_stub_returns_null', async () => {
    const api = { dummy: true };
    const onRestored = vi.fn();

    const { unmount } = render(<MatrixTopRegion api={api} projectUuid="proj_1" onRestored={onRestored} />);
    const emptyRegion = screen.getByTestId('matrix-top-region');
    expect(emptyRegion.children.length).toBe(0);
    expect(emptyRegion.textContent).toBe('');
    unmount();

    vi.resetModules();
    vi.doMock('./RestoreSessionBanner', () => ({
      RestoreSessionBanner: (props) => (
        <div
          data-slot="restore-banner"
          data-project={props.projectUuid}
          data-has-callback={String(props.onRestored === onRestored)}
        />
      )
    }));

    const { MatrixTopRegion: MockedMatrixTopRegion } = await import('./MatrixTopRegion');
    render(<MockedMatrixTopRegion api={api} projectUuid="proj_1" onRestored={onRestored} />);

    const region = screen.getByTestId('matrix-top-region');
    expect(region.children.length).toBe(1);
    expect(region.children[0].getAttribute('data-slot')).toBe('restore-banner');
    expect(region.children[0].getAttribute('data-project')).toBe('proj_1');
    expect(region.children[0].getAttribute('data-has-callback')).toBe('true');
  });
});
