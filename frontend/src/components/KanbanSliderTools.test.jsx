// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';
import { KanbanSliderTools, computeBoardMetrics } from './KanbanSliderTools';
import { ViewSliderProvider } from './ViewSlider';

afterEach(cleanup);

const NOW = Date.parse('2026-11-15T12:00:00Z');
const CARDS = [
  { card_id: 'A', status: 'backlog' },
  { card_id: 'B', status: 'in_progress', assignee_node_uuid: 'n1', lease_expires_at: '2026-11-15T13:00:00Z' },
  { card_id: 'C', status: 'in_progress', assignee_node_uuid: 'n2', lease_expires_at: '2026-11-15T11:00:00Z' },
  { card_id: 'D', status: 'consolidated', assignee_node_uuid: 'n1' }
];

describe('computeBoardMetrics', () => {
  it('cuenta por estado (consolidated cuenta como merged), sin asignar y leases vigentes', () => {
    const m = computeBoardMetrics(CARDS, NOW);
    expect(m.total).toBe(4);
    expect(m.byStatus).toEqual({ backlog: 1, in_progress: 2, in_review: 0, merged: 1 });
    expect(m.unassigned).toBe(1);
    expect(m.activeLeases).toBe(1);
  });
});

describe('KanbanSliderTools', () => {
  function setup(extra = {}) {
    const props = {
      cards: CARDS,
      filteredCount: 3,
      availableTags: ['okf'],
      availableNodes: ['n1'],
      availableBranches: ['feat/x'],
      selectedTag: 'all',
      onTagChange: vi.fn(),
      nodeStatusFilter: 'all',
      onNodeStatusChange: vi.fn(),
      branchFilter: 'all',
      onBranchChange: vi.fn(),
      onNewTask: vi.fn(),
      ...extra
    };
    render(
      <ViewSliderProvider viewKey="kanban">
        <KanbanSliderTools {...props} />
      </ViewSliderProvider>
    );
    return props;
  }

  it('Nueva Tarea cierra el slider y llama a onNewTask', () => {
    const props = setup();
    fireEvent.click(screen.getByTestId('view-slider-tab'));
    expect(screen.getByTestId('view-slider-tab').getAttribute('aria-expanded')).toBe('true');
    fireEvent.click(screen.getByTestId('kanban-new-task-btn'));
    expect(props.onNewTask).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('view-slider-tab').getAttribute('aria-expanded')).toBe('false');
  });

  it('los filtros de tag, nodo/estado y rama notifican el cambio y las metricas se muestran', () => {
    const props = setup();
    fireEvent.change(screen.getByTestId('workspace-tag-filter'), { target: { value: 'okf' } });
    fireEvent.change(screen.getByTestId('workspace-node-status-filter'), { target: { value: 'status:backlog' } });
    fireEvent.change(screen.getByTestId('workspace-branch-filter'), { target: { value: 'feat/x' } });
    expect(props.onTagChange).toHaveBeenCalledWith('okf');
    expect(props.onNodeStatusChange).toHaveBeenCalledWith('status:backlog');
    expect(props.onBranchChange).toHaveBeenCalledWith('feat/x');
    expect(screen.getByTestId('kanban-board-metrics').textContent).toContain('3 / 4');
  });
});
