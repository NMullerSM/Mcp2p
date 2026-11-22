import { useEffect, useState } from 'react';

const EMPTY = Object.freeze({ channels: [], participants: [] });

/**
 * Canales y participantes del proyecto (para los chips y el autocompletado de menciones).
 * Se carga al montar y al cambiar de proyecto. Nunca lanza: el fallo queda en `error`.
 */
export function useChatChannels({ api, projectUuid }) {
  const [state, setState] = useState({ channels: [], participants: [], error: null });

  // Dependencias primitivas (ver useChatFeed): un objeto nuevo en cada render no relanza la carga.
  const readable = typeof api?.chat_get_channels === 'function';

  useEffect(() => {
    let cancelled = false;
    if (!readable || !projectUuid) {
      setState({ ...EMPTY, error: null });
      return undefined;
    }
    (async () => {
      try {
        const res = await api.chat_get_channels(projectUuid);
        if (cancelled) return;
        if (res && res.success) {
          setState({
            channels: Array.isArray(res.channels) ? res.channels : [],
            participants: Array.isArray(res.participants) ? res.participants : [],
            error: null
          });
        } else {
          setState({ ...EMPTY, error: res?.message || 'No se pudieron cargar los canales del chat.' });
        }
      } catch (e) {
        if (!cancelled) setState({ ...EMPTY, error: e?.message || String(e) });
      }
    })();
    return () => {
      cancelled = true;
    };
    // `api` se lee de la carga actual; solo importa si cambia la posibilidad de leer o el proyecto.
  }, [readable, projectUuid]);

  return { channels: state.channels, participants: state.participants, error: state.error };
}
