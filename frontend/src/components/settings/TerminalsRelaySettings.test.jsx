// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import { TerminalsRelaySettings } from './TerminalsRelaySettings';
import { HINT_TEXTS } from './helpTexts';

const EXPECTED_ANCHORS = [
  'ayuda-auto-relay',
  'ayuda-modo-trabajador',
  'ayuda-entrega-directa',
  'ayuda-roles',
  'ayuda-mandato',
  'ayuda-otros-equipos',
  'ayuda-sesiones',
  'ayuda-confirmaciones'
];

const EXPECTED_SLOTS = [
  'ajustes-entrega',
  'ajustes-sesiones',
  'ajustes-confirmaciones',
  'ajustes-nodos'
];

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe('TerminalsRelaySettings', () => {
  it('renders_all_help_anchors', () => {
    const { container } = render(<TerminalsRelaySettings api={{}} />);

    expect(HINT_TEXTS.autoRelay.length).toBeLessThanOrEqual(140);
    expect(HINT_TEXTS.workerMode.length).toBeLessThanOrEqual(140);

    for (const anchorId of EXPECTED_ANCHORS) {
      const section = container.querySelector(`#${anchorId}`);
      expect(section).toBeTruthy();
    }

    for (const slotId of EXPECTED_SLOTS) {
      expect(screen.getByTestId(slotId)).toBeTruthy();
      expect(container.querySelector(`#${slotId}`)).toBeTruthy();
    }

    const entrega = container.querySelector('#ayuda-entrega-directa').textContent;
    expect(entrega).toContain('Solo con CLIs medidos, nunca a terminales con permisos saltados, nunca mientras escribes tú.');
    expect(entrega).toContain('El mensaje queda siempre guardado y auditado en la bandeja.');

    const roles = container.querySelector('#ayuda-roles').textContent;
    expect(roles).toContain('El rol lo eliges tú y decide qué tipos de mensaje puede enviar cada agente.');

    const mandato = container.querySelector('#ayuda-mandato').textContent;
    expect(mandato).toContain('El mandato es una instrucción para el agente, no un candado: MCP2P solo lo refuerza donde el CLI tiene una opción de solo lectura.');

    const otrosEquipos = container.querySelector('#ayuda-otros-equipos').textContent;
    expect(otrosEquipos).toContain('En esta versión aún no se reciben mensajes de otros equipos');
  });

  it('scrolls_to_initial_anchor', () => {
    const scrollIntoViewMock = vi.fn();
    const originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    HTMLElement.prototype.scrollIntoView = scrollIntoViewMock;

    try {
      const { container } = render(
        <TerminalsRelaySettings api={{}} initialAnchor="ayuda-entrega-directa" />
      );

      expect(scrollIntoViewMock).toHaveBeenCalledWith({ block: 'start' });
      const section = container.querySelector('#ayuda-entrega-directa');
      const heading = section.querySelector('h5');
      expect(heading.getAttribute('tabindex')).toBe('-1');
      expect(document.activeElement).toBe(heading);
    } finally {
      HTMLElement.prototype.scrollIntoView = originalScrollIntoView;
    }
  });
});
