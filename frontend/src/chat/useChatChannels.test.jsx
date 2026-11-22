// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, waitFor, cleanup } from '@testing-library/react';
import { useChatChannels } from './useChatChannels';

const CHANNELS = [
  { kind: 'agent', label: 'Agentes locales', available: true, reason: null },
  { kind: 'node_devs', label: 'Nodos', available: false, reason: 'Sin nodos remotos conectados' }
];
const PARTICIPANTS = [
  { principal: 'agent:orq', label: 'Orquestador', mention: '@orquestador', kind: 'agent', role: 'orchestrator', role_label: 'orquestador', cli_profile: null }
];

afterEach(cleanup);

describe('useChatChannels', () => {
  it('carga canales y participantes al montar', async () => {
    const api = { chat_get_channels: vi.fn(async () => ({ success: true, channels: CHANNELS, participants: PARTICIPANTS })) };
    const { result } = renderHook(() => useChatChannels({ api, projectUuid: 'p1' }));
    await waitFor(() => expect(result.current.channels).toEqual(CHANNELS));
    expect(result.current.participants).toEqual(PARTICIPANTS);
    expect(result.current.error).toBeNull();
    expect(api.chat_get_channels).toHaveBeenCalledWith('p1');
  });

  it('vuelve a cargar al cambiar de proyecto', async () => {
    const api = { chat_get_channels: vi.fn(async () => ({ success: true, channels: CHANNELS, participants: [] })) };
    const { rerender } = renderHook(({ uuid }) => useChatChannels({ api, projectUuid: uuid }), {
      initialProps: { uuid: 'p1' }
    });
    await waitFor(() => expect(api.chat_get_channels).toHaveBeenCalledTimes(1));
    rerender({ uuid: 'p2' });
    await waitFor(() => expect(api.chat_get_channels).toHaveBeenCalledTimes(2));
    expect(api.chat_get_channels).toHaveBeenLastCalledWith('p2');
  });

  it('sin el metodo en el puente no llama a nada y devuelve listas vacias', () => {
    const { result } = renderHook(() => useChatChannels({ api: {}, projectUuid: 'p1' }));
    expect(result.current).toEqual({ channels: [], participants: [], error: null });
  });

  it('sin proyecto no pregunta', () => {
    const api = { chat_get_channels: vi.fn(async () => ({ success: true, channels: [], participants: [] })) };
    renderHook(() => useChatChannels({ api, projectUuid: null }));
    expect(api.chat_get_channels).not.toHaveBeenCalled();
  });

  it('un fallo llega como error con texto y no lanza', async () => {
    const api = { chat_get_channels: vi.fn(async () => { throw new Error('sin puente'); }) };
    const { result } = renderHook(() => useChatChannels({ api, projectUuid: 'p1' }));
    await waitFor(() => expect(result.current.error).toBe('sin puente'));
    expect(result.current.channels).toEqual([]);
  });

  it('success:false muestra su mensaje', async () => {
    const api = { chat_get_channels: vi.fn(async () => ({ success: false, message: 'Proyecto desconocido' })) };
    const { result } = renderHook(() => useChatChannels({ api, projectUuid: 'p1' }));
    await waitFor(() => expect(result.current.error).toBe('Proyecto desconocido'));
  });

  it('descarta una respuesta que llega tras cambiar de proyecto', async () => {
    let resolveOld;
    const api = {
      chat_get_channels: vi.fn()
        .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
        .mockResolvedValue({ success: true, channels: [CHANNELS[0]], participants: [] })
    };
    const { result, rerender } = renderHook(({ uuid }) => useChatChannels({ api, projectUuid: uuid }), {
      initialProps: { uuid: 'viejo' }
    });
    rerender({ uuid: 'nuevo' });
    await waitFor(() => expect(result.current.channels).toEqual([CHANNELS[0]]));
    resolveOld({ success: true, channels: CHANNELS, participants: PARTICIPANTS });
    await new Promise((r) => setTimeout(r, 0));
    expect(result.current.channels).toEqual([CHANNELS[0]]);
    expect(result.current.participants).toEqual([]);
  });
});
