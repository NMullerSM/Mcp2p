// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import { PaneProvenance } from './PaneProvenance';

afterEach(cleanup);

describe('PaneProvenance', () => {
  it('no pinta nada si no hay identidad ni estado', () => {
    const { container } = render(<PaneProvenance paneId="pane_1" nodeUuid="" />);
    expect(container.innerHTML).toBe('');
  });

  it('muestra la identidad estable corta con la completa en el tooltip', () => {
    render(<PaneProvenance paneId="pane_1" nodeUuid="pane-0123456789abcdef" />);
    const node = screen.getByTestId('pane-provenance-node-pane_1');
    expect(node.textContent).toBe('pane-0123456');
    expect(node.getAttribute('title')).toContain('pane-0123456789abcdef');
    expect(screen.queryByTestId('pane-provenance-paused-pane_1')).toBeNull();
  });

  it('indica la pausa del propio panel', () => {
    render(<PaneProvenance paneId="pane_2" nodeUuid="n2" relayPaused />);
    const paused = screen.getByTestId('pane-provenance-paused-pane_2');
    expect(paused.textContent).toBe('Relevo en pausa');
    expect(paused.getAttribute('title')).toContain('solo usted puede reanudarlo');
  });

  it('la parada global se distingue de la pausa del panel', () => {
    render(<PaneProvenance paneId="pane_3" nodeUuid="n3" globalPaused />);
    expect(screen.getByTestId('pane-provenance-paused-pane_3').textContent).toBe('Relevo detenido');
  });
});
