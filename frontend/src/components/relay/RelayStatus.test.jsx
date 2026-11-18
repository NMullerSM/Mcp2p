// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';
import { RelayStatus } from './RelayStatus';

afterEach(cleanup);

const AUDIT_OK = {
  success: true,
  chain: { ok: true, rows: 12, first_bad_seq: null, reason: null },
  messages: { queued: 1, delivered: 2, parked: 3 },
  events: { submitted: 6 },
  max_chain_depth: 1,
  retention_days: 30,
  device: { storage: 'encrypted_file', has_certificate: false, warning: 'La clave está en un archivo cifrado.' }
};

describe('RelayStatus', () => {
  it('cargando: avisa que comprueba la auditoria y no inventa un veredicto', () => {
    render(<RelayStatus loading audit={null} globalPaused={false} />);
    expect(screen.getByTestId('relay-audit-loading')).toBeTruthy();
    expect(screen.queryByTestId('relay-audit-chain')).toBeNull();
  });

  it('cadena valida: lo dice con el numero de registros y los conteos por estado', () => {
    render(<RelayStatus audit={AUDIT_OK} globalPaused={false} />);
    const chain = screen.getByTestId('relay-audit-chain');
    expect(chain.getAttribute('data-chain-ok')).toBe('true');
    expect(chain.textContent).toContain('12 registros');
    expect(screen.getByTestId('relay-count-parked').textContent).toBe('3 retenidos');
    expect(screen.getByTestId('relay-count-queued').textContent).toBe('1 en cola');
    expect(screen.getByTestId('relay-device').textContent).toContain('archivo cifrado');
    expect(screen.getByTestId('relay-device').textContent).toContain('clave humana está bloqueada');
  });

  it('cadena rota: alerta con el registro, el motivo y que hacer', () => {
    render(
      <RelayStatus
        audit={{ ...AUDIT_OK, chain: { ok: false, rows: 12, first_bad_seq: 7, reason: 'ROW_HASH_MISMATCH' } }}
        globalPaused={false}
      />
    );
    const chain = screen.getByTestId('relay-audit-chain');
    expect(chain.getAttribute('data-chain-ok')).toBe('false');
    expect(chain.getAttribute('role')).toBe('alert');
    expect(chain.textContent).toContain('n.º 7');
    expect(chain.textContent).toContain('ROW_HASH_MISMATCH');
    expect(chain.textContent).toContain('Conserve una copia');
  });

  it('error al leer: muestra la causa', () => {
    render(<RelayStatus audit={null} auditError="No se pudo leer el estado de la auditoría: reinicie MCP2P." globalPaused={false} />);
    expect(screen.getByTestId('relay-audit-error').textContent).toContain('reinicie MCP2P');
  });

  it('el interruptor de parada global refleja el estado y pide invertirlo', () => {
    const onSet = vi.fn();
    const { rerender } = render(<RelayStatus audit={AUDIT_OK} globalPaused={false} onSetGlobalPause={onSet} />);
    const toggle = screen.getByTestId('relay-global-pause-switch');
    expect(toggle.getAttribute('role')).toBe('switch');
    expect(toggle.getAttribute('aria-checked')).toBe('false');
    fireEvent.click(toggle);
    expect(onSet).toHaveBeenLastCalledWith(true);
    rerender(<RelayStatus audit={AUDIT_OK} globalPaused onSetGlobalPause={onSet} />);
    expect(screen.getByTestId('relay-global-pause-switch').getAttribute('aria-checked')).toBe('true');
    expect(screen.getByTestId('relay-pause-description').textContent).toContain('Ningún agente recibe ni envía');
    fireEvent.click(screen.getByTestId('relay-global-pause-switch'));
    expect(onSet).toHaveBeenLastCalledWith(false);
  });

  it('ocupado o sin accion disponible: el interruptor no se puede pulsar', () => {
    const { rerender } = render(<RelayStatus audit={AUDIT_OK} globalPaused={false} onSetGlobalPause={vi.fn()} busy />);
    expect(screen.getByTestId('relay-global-pause-switch').disabled).toBe(true);
    rerender(<RelayStatus audit={AUDIT_OK} globalPaused={false} />);
    expect(screen.getByTestId('relay-global-pause-switch').disabled).toBe(true);
  });

  it('muestra el resultado de la ultima accion', () => {
    render(<RelayStatus audit={AUDIT_OK} globalPaused feedback={{ tone: 'warning', text: 'Relevo detenido: nadie recibe ni envía mensajes.' }} />);
    expect(screen.getByTestId('relay-status-feedback').textContent).toContain('Relevo detenido');
  });

  it('con pausedPanes vacio no pinta relay-paused-panes', () => {
    const { rerender } = render(<RelayStatus audit={AUDIT_OK} globalPaused={false} pausedPanes={[]} />);
    expect(screen.queryByTestId('relay-paused-panes')).toBeNull();
    rerender(<RelayStatus audit={AUDIT_OK} globalPaused={false} />);
    expect(screen.queryByTestId('relay-paused-panes')).toBeNull();
  });

  it('lista paneles en pausa y muestra el boton Reanudar cuando hay accion', () => {
    const onResume = vi.fn();
    render(
      <RelayStatus
        audit={AUDIT_OK}
        globalPaused={false}
        pausedPanes={['pane-1234567890ab']}
        onResumePane={onResume}
      />
    );
    const section = screen.getByTestId('relay-paused-panes');
    expect(section.textContent).toContain('Paneles con relevo en pausa');
    const idChip = screen.getByTitle('pane-1234567890ab');
    expect(idChip.textContent).toBe('pane-1234567');
    const btn = screen.getByTestId('relay-resume-pane-pane-1234567890ab');
    expect(btn.textContent).toBe('Reanudar');
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(onResume).toHaveBeenCalledWith('pane-1234567890ab');
  });

  it('deshabilita el boton al reanudar u omite el boton si no hay onResumePane', () => {
    const { rerender } = render(
      <RelayStatus
        audit={AUDIT_OK}
        globalPaused={false}
        pausedPanes={['pane-1234567890ab', 'pane-abcdef123456']}
        resumingNode="pane-1234567890ab"
        onSetGlobalPause={vi.fn()}
        onResumePane={vi.fn()}
      />
    );
    const activeBtn = screen.getByTestId('relay-resume-pane-pane-1234567890ab');
    const otherBtn = screen.getByTestId('relay-resume-pane-pane-abcdef123456');
    expect(activeBtn.textContent).toBe('Reanudando…');
    expect(activeBtn.disabled).toBe(true);
    expect(otherBtn.disabled).toBe(true);
    expect(screen.getByTestId('relay-global-pause-switch').disabled).toBe(true);

    rerender(<RelayStatus audit={AUDIT_OK} globalPaused={false} pausedPanes={['pane-1234567890ab']} />);
    expect(screen.queryByTestId('relay-resume-pane-pane-1234567890ab')).toBeNull();
  });
});
