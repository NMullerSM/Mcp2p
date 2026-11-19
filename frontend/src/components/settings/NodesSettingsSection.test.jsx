// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import React from 'react';
import { NodesSettingsSection } from './NodesSettingsSection';

afterEach(cleanup);

describe('NodesSettingsSection', () => {
  it('renders_local_node_and_known_remote_nodes', async () => {
    const api = {
      relay_get_local_node: vi.fn(async () => ({
        success: true,
        display_name: 'Mi Estación',
        fingerprint: '1122334455667788',
        fingerprint_short: '1122-3344-…-5566-7788',
        human_key_available: true
      })),
      relay_list_nodes: vi.fn(async () => ({
        success: true,
        nodes: [
          {
            device_fingerprint: 'aabbccddeeff0011',
            fingerprint_short: 'AABB-CCDD-…-EEFF-0011',
            display_name: 'Portátil',
            relation: 'foreign',
            policy: 'ask_each',
            name_collision: false,
            previous_name: null
          }
        ]
      })),
      relay_set_node_policy: vi.fn(async () => ({ success: true })),
      relay_set_local_node_name: vi.fn(async (name) => ({ success: true, display_name: name }))
    };

    await act(async () => {
      render(<NodesSettingsSection api={api} />);
    });

    expect(screen.getByTestId('nodes-local-name-input').value).toBe('Mi Estación');
    expect(screen.getByTestId('nodes-local-fingerprint').textContent).toContain('1122-3344-…-5566-7788');
    expect(screen.getByTestId('nodes-item-aabbccddeeff0011').textContent).toContain('Portátil');
    expect(screen.getByTestId('nodes-item-aabbccddeeff0011').textContent).toContain('Ajeno');
    expect(screen.getByTestId('nodes-fp-aabbccddeeff0011').textContent).toBe('AABB-CCDD-…-EEFF-0011');

    const select = screen.getByTestId('nodes-policy-select-aabbccddeeff0011');
    expect(select.value).toBe('ask_each');

    await act(async () => {
      fireEvent.change(select, { target: { value: 'direct' } });
    });
    expect(api.relay_set_node_policy).toHaveBeenCalledWith('aabbccddeeff0011', 'direct');

    await act(async () => {
      fireEvent.change(screen.getByTestId('nodes-local-name-input'), {
        target: { value: 'Estación Nueva' }
      });
      fireEvent.click(screen.getByTestId('nodes-local-name-save'));
    });
    expect(api.relay_set_local_node_name).toHaveBeenCalledWith('Estación Nueva');
  });

  it('shows_declined_confirmation_message_when_trust_direct_declined', async () => {
    const api = {
      relay_get_local_node: vi.fn(async () => ({
        success: true,
        display_name: 'Mi Estación',
        fingerprint: '1122334455667788',
        human_key_available: true
      })),
      relay_list_nodes: vi.fn(async () => ({
        success: true,
        nodes: [
          {
            device_fingerprint: 'aabbccddeeff0011',
            display_name: 'Portátil',
            relation: 'foreign',
            policy: 'ask_each'
          }
        ]
      })),
      relay_set_node_policy: vi.fn(async () => ({
        success: false,
        code: 'CONFIRMATION_DECLINED',
        message: 'Rechazada'
      }))
    };

    await act(async () => {
      render(<NodesSettingsSection api={api} />);
    });

    await act(async () => {
      fireEvent.change(screen.getByTestId('nodes-policy-select-aabbccddeeff0011'), {
        target: { value: 'direct' }
      });
    });

    expect(screen.getByTestId('nodes-settings-feedback').textContent).toContain('Rechazó el diálogo');
  });
});
