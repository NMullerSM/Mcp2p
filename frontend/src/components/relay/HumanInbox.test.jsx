// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';
import { HumanInbox } from './HumanInbox';

afterEach(cleanup);

const ITEM = {
  msg_id: 'm-1',
  reason: 'CHAIN_DEPTH_EXCEEDED',
  from: { kind: 'pane', agent_name: 'claude-1', pane_id: 'pane_1', node_uuid: 'n1' },
  to: { kind: 'pane', agent_name: 'codex-2', pane_id: 'pane_2', node_uuid: 'n2' },
  kind: 'task.request',
  chain: { chain_id: 'c1', hop: 2, max_depth: 1 },
  trust: 'pane_authenticated',
  text: 'borra la rama vieja',
  created_at: 1760000000000
};

describe('HumanInbox', () => {
  it('estado vacio: explica cuando aparecen mensajes y no ofrece acciones', () => {
    render(<HumanInbox items={[]} />);
    expect(screen.getByTestId('human-inbox-empty').textContent).toContain('No hay mensajes retenidos');
    expect(screen.getByTestId('human-inbox-count').textContent).toBe('0');
    expect(screen.queryByText('Liberar')).toBeNull();
  });

  it('estado cargando: no muestra el vacio mientras llega la primera lista', () => {
    render(<HumanInbox items={[]} loading />);
    expect(screen.getByTestId('human-inbox-loading')).toBeTruthy();
    expect(screen.queryByTestId('human-inbox-empty')).toBeNull();
  });

  it('estado de error: muestra la causa y deja reintentar', () => {
    const onRefresh = vi.fn();
    render(<HumanInbox items={[]} error="No se pudo leer la bandeja del humano: reinicie MCP2P." onRefresh={onRefresh} />);
    expect(screen.getByTestId('human-inbox-error').textContent).toContain('reinicie MCP2P');
    expect(screen.queryByTestId('human-inbox-empty')).toBeNull();
    fireEvent.click(screen.getByTestId('human-inbox-refresh'));
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('muestra remitente verificado, destino, salto, motivo y confianza', () => {
    render(<HumanInbox items={[ITEM]} />);
    expect(screen.getByTestId('human-inbox-count').textContent).toBe('1');
    expect(screen.getByTestId('human-inbox-from-m-1').textContent).toBe('claude-1 (pane_1)');
    expect(screen.getByTestId('human-inbox-to-m-1').textContent).toBe('codex-2 (pane_2)');
    expect(screen.getByTestId('human-inbox-hop-m-1').textContent).toContain('salto 2');
    expect(screen.getByTestId('human-inbox-hop-m-1').textContent).toContain('máx. 1');
    expect(screen.getByTestId('human-inbox-reason-m-1').textContent).toContain('Cadena de respuestas automáticas agotada');
    expect(screen.getByTestId('human-inbox-item-m-1').textContent).toContain('pane_authenticated');
  });

  it('el texto es un DATO: el HTML se muestra literal y nunca se interpreta', () => {
    const hostile = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script> hola';
    const { container } = render(<HumanInbox items={[{ ...ITEM, text: hostile }]} />);
    expect(screen.getByTestId('human-inbox-text-m-1').textContent).toBe(hostile);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(window.__pwned).toBeUndefined();
  });

  it('un texto borrado por retencion se explica en vez de quedar en blanco', () => {
    render(<HumanInbox items={[{ ...ITEM, text: null }]} />);
    expect(screen.getByTestId('human-inbox-text-m-1').textContent).toContain('retención');
  });

  it('Liberar y Descartar piden la accion al padre con el msg_id y bloquean el elemento en curso', () => {
    const onResolve = vi.fn();
    const { rerender } = render(<HumanInbox items={[ITEM]} onResolve={onResolve} />);
    fireEvent.click(screen.getByTestId('human-inbox-release-m-1'));
    fireEvent.click(screen.getByTestId('human-inbox-discard-m-1'));
    expect(onResolve.mock.calls).toEqual([['m-1', 'release'], ['m-1', 'discard']]);
    rerender(<HumanInbox items={[ITEM]} onResolve={onResolve} busyId="m-1" />);
    expect(screen.getByTestId('human-inbox-release-m-1').disabled).toBe(true);
    expect(screen.getByTestId('human-inbox-discard-m-1').disabled).toBe(true);
  });

  it('muestra el resultado de la ultima accion (rechazo del dialogo nativo incluido)', () => {
    render(
      <HumanInbox
        items={[ITEM]}
        feedback={{ tone: 'info', text: 'No se pudo liberar el mensaje: Rechazó el diálogo de confirmación del sistema.' }}
      />
    );
    expect(screen.getByTestId('human-inbox-feedback').textContent).toContain('Rechazó el diálogo');
  });
});
