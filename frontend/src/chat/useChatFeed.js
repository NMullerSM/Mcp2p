import { useCallback, useEffect, useRef, useState } from 'react';
import { mergeEntries } from './chatModel';

// Con el panel abierto se sondea cada 3 s; cerrado, cada 10 s. Con la ventana oculta no se pregunta.
export const POLL_OPEN_MS = 3000;
export const POLL_CLOSED_MS = 10000;
export const FEED_PAGE_LIMIT = 200;
const LAST_SEEN_PREFIX = 'mcp2p.chat.lastSeen.';

export function lastSeenKey(projectUuid) {
  return LAST_SEEN_PREFIX + projectUuid;
}

// localStorage puede no existir o estar bloqueado: la interfaz debe funcionar igual sin el.
function readLastSeen(projectUuid) {
  try {
    const value = Number(window.localStorage.getItem(lastSeenKey(projectUuid)));
    return Number.isFinite(value) && value > 0 ? value : 0;
  } catch {
    return 0;
  }
}

function writeLastSeen(projectUuid, value) {
  try {
    window.localStorage.setItem(lastSeenKey(projectUuid), String(value));
  } catch {
    // Sin persistencia el contador de no leidos solo dura lo que la sesion.
  }
}

function canReadFeed(api) {
  return typeof api?.chat_get_feed === 'function';
}

function isVisible() {
  return typeof document !== 'undefined' && document.visibilityState === 'visible';
}

function latestTs(entries) {
  let latest = 0;
  for (const entry of entries) {
    const ts = Number(entry?.ts_ms);
    if (Number.isFinite(ts) && ts > latest) latest = ts;
  }
  return latest;
}

/**
 * Feed del chat por sondeo incremental. Nunca lanza: los fallos quedan en `error`.
 * Devuelve `{entries, sources, error, loading, refresh, lastSeenMs, markSeen}`.
 */
export function useChatFeed({ api, projectUuid, open = false }) {
  const [entries, setEntries] = useState([]);
  const [sources, setSources] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [lastSeenMs, setLastSeenMs] = useState(() => (projectUuid ? readLastSeen(projectUuid) : 0));
  // Las dependencias de los efectos son valores primitivos: un llamador que pase un objeto nuevo
  // en cada render no debe reiniciar el feed en bucle.
  const readable = canReadFeed(api);

  // Referencias para que las llamadas en vuelo lean siempre el proyecto y el puente actuales.
  const apiRef = useRef(api);
  const uuidRef = useRef(projectUuid);
  const entriesRef = useRef(entries);
  const cursorRef = useRef(0);
  const lastSeenRef = useRef(lastSeenMs);
  // Cada cambio de proyecto abre una "epoca": las respuestas de la anterior se descartan.
  const epochRef = useRef(0);
  const inFlightRef = useRef(null);
  apiRef.current = api;
  uuidRef.current = projectUuid;
  entriesRef.current = entries;

  const poll = useCallback(async () => {
    const currentApi = apiRef.current;
    const uuid = uuidRef.current;
    if (!canReadFeed(currentApi) || !uuid) return;
    // Sin solapamiento: si la llamada anterior sigue en vuelo, esta vez no se pregunta.
    if (inFlightRef.current !== null) return;
    const epoch = epochRef.current;
    inFlightRef.current = epoch;
    setLoading(true);
    try {
      const res = await currentApi.chat_get_feed(uuid, cursorRef.current, FEED_PAGE_LIMIT);
      if (epoch !== epochRef.current) return;
      if (res && res.success) {
        const incoming = Array.isArray(res.entries) ? res.entries : [];
        if (incoming.length > 0) setEntries((prev) => mergeEntries(prev, incoming));
        if (typeof res.cursor_ms === 'number' && Number.isFinite(res.cursor_ms)) {
          cursorRef.current = res.cursor_ms;
        }
        setSources(Array.isArray(res.sources) ? res.sources : []);
        setError(null);
      } else {
        setError(res?.message || 'No se pudo leer el chat.');
      }
    } catch (e) {
      if (epoch === epochRef.current) setError(e?.message || String(e));
    } finally {
      if (inFlightRef.current === epoch) inFlightRef.current = null;
      if (epoch === epochRef.current) setLoading(false);
    }
  }, []);

  // Cambio de proyecto (o de puente): estado y cursor desde cero, y primera lectura.
  useEffect(() => {
    epochRef.current += 1;
    inFlightRef.current = null;
    cursorRef.current = 0;
    const seen = projectUuid ? readLastSeen(projectUuid) : 0;
    lastSeenRef.current = seen;
    setLastSeenMs(seen);
    setEntries([]);
    setSources([]);
    setError(null);
    poll();
  }, [readable, projectUuid, poll]);

  // Sondeo periodico, y una lectura inmediata al volver a mostrar la ventana.
  useEffect(() => {
    if (!readable || !projectUuid) return undefined;
    const onVisible = () => {
      if (isVisible()) poll();
    };
    const timer = setInterval(onVisible, open ? POLL_OPEN_MS : POLL_CLOSED_MS);
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [readable, projectUuid, open, poll]);

  // Relee desde el principio: recupera los estados de entrega que cambiaron desde la ultima lectura.
  const refresh = useCallback(() => {
    cursorRef.current = 0;
    return poll();
  }, [poll]);

  const markSeen = useCallback((atMs) => {
    const latest = latestTs(entriesRef.current);
    const candidate = Number.isFinite(atMs) ? atMs : latest || Date.now();
    const value = Math.max(lastSeenRef.current, candidate);
    if (value === lastSeenRef.current) return;
    lastSeenRef.current = value;
    setLastSeenMs(value);
    if (uuidRef.current) writeLastSeen(uuidRef.current, value);
  }, []);

  return { entries, sources, error, loading, refresh, lastSeenMs, markSeen };
}
