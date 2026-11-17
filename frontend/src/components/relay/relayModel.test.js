import { describe, it, expect } from 'vitest';
import { describeRelayError, partyLabel, reasonLabel, shortId, storageLabel, utf8Length } from './relayModel';

describe('relayModel', () => {
  it('describeRelayError dice la causa y que hacer para un codigo conocido', () => {
    const text = describeRelayError({ success: false, code: 'MESSAGE_NOT_FOUND' }, 'liberar el mensaje');
    expect(text).toContain('No se pudo liberar el mensaje');
    expect(text).toContain('Actualice la bandeja');
  });

  it('usa el mensaje del backend si el codigo no es conocido y una causa generica si no hay nada', () => {
    expect(describeRelayError({ success: false, code: 'RARO', message: 'Algo concreto' }, 'enviar')).toBe(
      'No se pudo enviar: Algo concreto'
    );
    expect(describeRelayError({ success: false, error: 'boom' }, 'enviar')).toContain('boom');
    expect(describeRelayError({}, 'enviar')).toContain('Vuelva a intentarlo');
    expect(describeRelayError(new Error('puente caido'), 'enviar')).toContain('puente caido');
  });

  it('nombra al remitente: el humano es «Usted» y un panel lleva agente y terminal', () => {
    expect(partyLabel({ kind: 'human', principal: 'human:local' })).toBe('Usted');
    expect(partyLabel({ kind: 'pane', agent_name: 'claude-1', pane_id: 'pane_1' })).toBe('claude-1 (pane_1)');
    expect(partyLabel({ kind: 'pane', node_uuid: 'abcdefghijklmnop' })).toBe('abcdefghijkl');
    expect(partyLabel(null)).toBe('Desconocido');
  });

  it('etiquetas de motivo, huellas cortas, tamano en bytes UTF-8 y almacenamiento de la clave', () => {
    expect(reasonLabel('CHAIN_DEPTH_EXCEEDED')).toContain('agotada');
    expect(reasonLabel('HUMAN_CONFIRMATION_REQUESTED')).toContain('confirmación');
    expect(reasonLabel(null)).toBe('Retenido para el humano');
    expect(shortId('1234567890', 4)).toBe('1234');
    expect(utf8Length('añ')).toBe(3);
    expect(storageLabel('encrypted_file')).toBe('archivo cifrado');
    expect(storageLabel('keyring')).toBe('llavero del sistema');
  });
});
