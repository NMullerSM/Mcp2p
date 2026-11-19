// @vitest-environment jsdom
import { describe, it, expect, afterEach, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act } from '@testing-library/react';
import React from 'react';
import { NodeRequestCard } from './NodeRequestCard';

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

const BASE_ITEM = {
  msg_id: 'rem-1',
  reason: 'UNTRUSTED_NODE',
  from: { kind: 'pane', node_uuid: 'remote-pane-1', pane_id: null, agent_name: null },
  to: { kind: 'pane', node_uuid: 'local-pane-2', pane_id: 'pane_2', agent_name: 'codex-2' },
  kind: 'note',
  chain: { chain_id: 'c-rem-1', hop: 0, max_depth: 1 },
  trust: 'unverified',
  text: 'Hola desde el portátil',
  created_at: 1760000000000,
  expires_at: 1760086400000,
  remote: {
    display_name: 'Portátil',
    fingerprint_short: 'A1B2-C3D4-…-E5F6-0708',
    device_fingerprint: 'a1b2c3d4e5f60708',
    relation: 'foreign',
    transport: 'simulated',
    expires_at: 1760086400000,
    name_collision: false,
    previous_name: null
  }
};

describe('NodeRequestCard', () => {
  it('buttons_disabled_for_1500ms', () => {
    render(<NodeRequestCard item={BASE_ITEM} onResolve={vi.fn()} onSetNodePolicy={vi.fn()} />);

    const approve = screen.getByTestId('node-request-approve-rem-1');
    const trust = screen.getByTestId('node-request-trust-rem-1');
    const discard = screen.getByTestId('node-request-discard-rem-1');
    const block = screen.getByTestId('node-request-block-rem-1');

    expect(approve.disabled).toBe(true);
    expect(trust.disabled).toBe(true);
    expect(discard.disabled).toBe(true);
    expect(block.disabled).toBe(true);
    expect(document.activeElement).not.toBe(approve);
    expect(document.activeElement).not.toBe(trust);

    act(() => {
      vi.advanceTimersByTime(1499);
    });
    expect(approve.disabled).toBe(true);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(approve.disabled).toBe(false);
    expect(trust.disabled).toBe(false);
    expect(discard.disabled).toBe(false);
    expect(block.disabled).toBe(false);
  });

  it('rearms_1500ms_if_list_reorders_under_pointer', () => {
    const { rerender } = render(
      <NodeRequestCard item={BASE_ITEM} listOrderKey="pos-0" onResolve={vi.fn()} onSetNodePolicy={vi.fn()} />
    );
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    const card = screen.getByTestId('node-request-card-rem-1');
    const approve = screen.getByTestId('node-request-approve-rem-1');
    expect(approve.disabled).toBe(false);

    fireEvent.mouseEnter(card);
    rerender(
      <NodeRequestCard item={BASE_ITEM} listOrderKey="pos-1" onResolve={vi.fn()} onSetNodePolicy={vi.fn()} />
    );
    expect(approve.disabled).toBe(true);

    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(approve.disabled).toBe(false);
  });

  it('approve_calls_resolve_parked_release', async () => {
    const api = {
      relay_resolve_parked: vi.fn(async () => ({ success: true })),
      relay_set_node_policy: vi.fn(async () => ({ success: true }))
    };
    render(<NodeRequestCard item={BASE_ITEM} api={api} />);
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('node-request-approve-rem-1'));
    });
    expect(api.relay_resolve_parked).toHaveBeenCalledWith('rem-1', 'release');
  });

  it('trust_calls_set_node_policy_direct', async () => {
    const api = {
      relay_resolve_parked: vi.fn(async () => ({ success: true })),
      relay_set_node_policy: vi.fn(async () => ({ success: true }))
    };
    render(<NodeRequestCard item={BASE_ITEM} api={api} />);
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('node-request-trust-rem-1'));
    });
    expect(api.relay_set_node_policy).toHaveBeenCalledWith('a1b2c3d4e5f60708', 'direct');
  });

  it('block_calls_set_node_policy_blocked', async () => {
    const api = {
      relay_resolve_parked: vi.fn(async () => ({ success: true })),
      relay_set_node_policy: vi.fn(async () => ({ success: true }))
    };
    render(<NodeRequestCard item={BASE_ITEM} api={api} />);
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    await act(async () => {
      fireEvent.click(screen.getByTestId('node-request-block-rem-1'));
    });
    expect(api.relay_set_node_policy).toHaveBeenCalledWith('a1b2c3d4e5f60708', 'blocked');
  });

  it('collision_alert_visible', () => {
    const item = {
      ...BASE_ITEM,
      remote: { ...BASE_ITEM.remote, name_collision: true, previous_name: 'Viejo' }
    };
    render(<NodeRequestCard item={item} />);
    const alert = screen.getByTestId('node-request-collision-rem-1');
    expect(alert.getAttribute('role')).toBe('alert');
    expect(alert.textContent).toContain('Comprueba la huella antes de aprobar');
    expect(screen.getByTestId('node-request-prev-name-rem-1').textContent).toContain('antes: Viejo');
  });

  it('html_in_text_is_rendered_literally', () => {
    const hostile = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script> texto';
    const { container } = render(<NodeRequestCard item={{ ...BASE_ITEM, text: hostile }} />);
    const pre = screen.getByTestId('node-request-text-rem-1');
    expect(pre.tagName).toBe('PRE');
    expect(pre.style.whiteSpace).toBe('pre-wrap');
    expect(pre.textContent).toBe(hostile);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
  });

  it('fits_375px_and_separates_approval_from_rejection_buttons', () => {
    const { container } = render(<NodeRequestCard item={BASE_ITEM} />);
    const card = screen.getByTestId('node-request-card-rem-1');
    expect(card.style.maxWidth).toBe('100%');
    const approve = screen.getByTestId('node-request-approve-rem-1');
    const block = screen.getByTestId('node-request-block-rem-1');
    expect(approve.parentElement).not.toBe(block.parentElement);
    for (const el of container.querySelectorAll('[style]')) {
      const m = /^(\d+)px$/.exec(el.style.width || '');
      if (m) {
        expect(Number(m[1])).toBeLessThanOrEqual(343);
      }
    }
  });
});
