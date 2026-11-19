// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import { AgentStateBadge } from './AgentStateBadge';

afterEach(cleanup);

describe('AgentStateBadge', () => {
  it('renders_only_for_hook_source', () => {
    const { container, rerender } = render(<AgentStateBadge pane={{ paneId: 'pane_1', agentState: null }} />);
    expect(container.innerHTML).toBe('');

    for (const source of ['initial', 'human', 'injection', 'hub', '']) {
      rerender(
        <AgentStateBadge
          pane={{ paneId: 'pane_1', agentState: { state: 'working', source, since_ms: 1000 } }}
        />
      );
      expect(screen.queryByTestId('agent-state-badge-pane_1')).toBeNull();
    }

    for (const state of ['unknown', 'exited']) {
      rerender(
        <AgentStateBadge
          pane={{ paneId: 'pane_1', agentState: { state, source: 'hook', since_ms: 1000 } }}
        />
      );
      expect(screen.queryByTestId('agent-state-badge-pane_1')).toBeNull();
    }

    rerender(
      <AgentStateBadge
        pane={{ pane_id: 'pane_2', agent_state: { state: 'idle', source: 'hook', since_ms: 2000 } }}
      />
    );
    expect(screen.getByTestId('agent-state-badge-pane_2').textContent).toBe('Libre');
  });

  it('labels_in_spanish', () => {
    const cases = [
      ['idle', 'Libre'],
      ['working', 'Trabajando'],
      ['permission', 'Esperando permiso']
    ];
    for (const [state, label] of cases) {
      const { unmount } = render(
        <AgentStateBadge
          pane={{ paneId: 'pane_1', agentState: { state, source: 'hook', since_ms: 3000 } }}
        />
      );
      const badge = screen.getByTestId('agent-state-badge-pane_1');
      expect(badge.textContent).toBe(label);
      expect(badge.getAttribute('title')).toContain('según los hooks del CLI');
      unmount();
    }
  });
});
