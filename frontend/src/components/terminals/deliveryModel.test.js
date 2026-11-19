import { describe, it, expect } from 'vitest';
import {
  DELIVERY_REASON_TEXT,
  deliveryToggleLabel,
  describeDeliveryPlatform,
  describeDeliveryReason,
  nextDeliveryMode
} from './deliveryModel';

const REASON_CODES = [
  'GLOBAL_PAUSED', 'PANE_PAUSED', 'PANE_GONE', 'DELIVERY_MODE_INBOX', 'CLI_NOT_VERIFIED',
  'TARGET_ELEVATED', 'AGENT_IN_INBOX_WAIT', 'AGENT_BUSY', 'AGENT_PERMISSION', 'AGENT_STATE_UNKNOWN',
  'AWAITING_IDLE_AFTER_INJECTION', 'RECENT_OUTPUT', 'HUMAN_TYPING', 'IN_FLIGHT', 'REMOTE_POLICY',
  'MESSAGE_EXPIRED', 'TOO_LONG_POINTER'
];

describe('deliveryModel', () => {
  it('every_reason_code_has_spanish_text', () => {
    for (const code of REASON_CODES) {
      const text = describeDeliveryReason(code);
      expect(text, code).toBeTruthy();
      expect(text.trim().length, code).toBeGreaterThan(0);
      expect(DELIVERY_REASON_TEXT[code]).toBe(text);
    }
    expect(describeDeliveryReason('CLI_NOT_VERIFIED')).toBe('Este CLI aun no esta medido: solo bandeja.');
  });

  it('unknown_code_returns_empty_string', () => {
    expect(describeDeliveryReason('CODIGO_INVENTADO')).toBe('');
    expect(describeDeliveryReason('')).toBe('');
    expect(describeDeliveryReason(null)).toBe('');
    expect(describeDeliveryReason(undefined)).toBe('');
    expect(describeDeliveryReason('constructor')).toBe('');
    expect(describeDeliveryReason(42)).toBe('');
  });

  it('cycle_and_labels', () => {
    expect(nextDeliveryMode('auto')).toBe('direct');
    expect(nextDeliveryMode('direct')).toBe('inbox');
    expect(nextDeliveryMode('inbox')).toBe('auto');
    expect(nextDeliveryMode(undefined)).toBe('direct');

    expect(deliveryToggleLabel('direct', 'direct')).toBe('Entrega directa: ON');
    expect(deliveryToggleLabel('inbox', 'inbox')).toBe('Entrega directa: OFF');
    expect(deliveryToggleLabel('auto', 'inbox')).toBe('Entrega directa: AUTO');
    expect(deliveryToggleLabel(undefined)).toBe('Entrega directa: AUTO');

    expect(describeDeliveryPlatform('linux')).toBe('Linux');
    expect(describeDeliveryPlatform('darwin')).toBe('macOS');
    expect(describeDeliveryPlatform('win32')).toBe('Windows');
    expect(describeDeliveryPlatform('plan9')).toBe('Otro sistema');
  });
});
