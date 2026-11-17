/**
 * Modelo puro del panel de terminales (sin React): layout dinamico, ritmo del sondeo de la
 * salida del PTY, eleccion de pane libre, memoria de sesiones y mapeo de codigos de error del
 * backend a mensajes con la accion sugerida (seccion 4 del plan 2026-11-15).
 *
 * Nada de lo que hay aqui construye ni edita comandos de instalacion: el frontend solo
 * muestra lo que devuelve el backend.
 */

export const HARD_MAX_PANES = 6;
export const DEFAULT_MAX_PANES = 4;
export const MATRIX_PANE_IDS = Array.from({ length: HARD_MAX_PANES }, (_, i) => `pane_${i + 1}`);

export const ROLE_OPTIONS = [
  { value: 'implementer', label: 'Implementer' },
  { value: 'reviewer', label: 'Reviewer' },
  { value: 'auditor', label: 'Auditor' },
  { value: 'coordinator', label: 'Coordinator' }
];

// Tamano con el que se lanza el PTY. Despues manda el panel: xterm lo ajusta con fit() y se
// avisa al backend con pty_resize_pane (ya no hay presets manuales).
export const DEFAULT_PTY_SIZE = { cols: 100, rows: 28 };

export const DEFAULT_MANUAL_COMMAND =
  'mcp2p --mcp-server --transport streamable-http --host 127.0.0.1 --port 8765';

export const DEFAULT_MCP_CONFIG = {
  mcpServers: {
    mcp2p: {
      url: 'http://127.0.0.1:8765/mcp'
    }
  }
};

// ---------------------------------------------------------------------------
// Layout dinamico
// ---------------------------------------------------------------------------

/**
 * Cuadricula segun cuantas terminales hay abiertas:
 * 0-1 -> pantalla completa, 2 -> dos columnas, 3-4 -> 2x2, 5-6 -> 3x2.
 * `cells` es el numero de celdas de la cuadricula (las que sobran son paneles vacios).
 */
export function computeGridLayout(openCount) {
  const n = Math.max(1, Math.min(HARD_MAX_PANES, Number.isFinite(openCount) ? Math.floor(openCount) : 1));
  let cols;
  let rows;
  if (n <= 1) {
    cols = 1;
    rows = 1;
  } else if (n === 2) {
    cols = 2;
    rows = 1;
  } else if (n <= 4) {
    cols = 2;
    rows = 2;
  } else {
    cols = 3;
    rows = 2;
  }
  return {
    id: `${cols}x${rows}`,
    cols,
    rows,
    cells: cols * rows,
    columns: `repeat(${cols}, minmax(0, 1fr))`,
    rowTemplate: `repeat(${rows}, minmax(0, 1fr))`
  };
}

/** Primer `pane_N` (N <= maxPanes) que no esta abierto, o null si no queda ninguno. */
export function pickFreePaneId(openPaneIds, maxPanes = DEFAULT_MAX_PANES) {
  const taken = new Set(openPaneIds);
  const limit = Math.max(1, Math.min(HARD_MAX_PANES, Number(maxPanes) || DEFAULT_MAX_PANES));
  for (let i = 1; i <= limit; i += 1) {
    const id = `pane_${i}`;
    if (!taken.has(id)) return id;
  }
  return null;
}

export function paneNumber(paneId) {
  const m = /^pane_(\d+)$/.exec(String(paneId || ''));
  return m ? Number(m[1]) : null;
}

// ---------------------------------------------------------------------------
// Salida del terminal
// ---------------------------------------------------------------------------

// La salida del PTY (con sus secuencias ANSI) se escribe tal cual en xterm: la interpreta el
// emulador. Esta funcion solo sirve para texto plano fuera de la terminal (p. ej. busquedas).
const ANSI_ESCAPE_REGEX =
  /\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][0-9A-Za-z]|\x1b[@-Z\\-_]/g;

export function stripAnsiSequences(text) {
  if (!text) return '';
  return String(text).replace(ANSI_ESCAPE_REGEX, '');
}

// ---------------------------------------------------------------------------
// Sondeo adaptativo de la salida
// ---------------------------------------------------------------------------

export const POLL_FAST_MS = 30;
export const POLL_WARM_MS = 100;
export const POLL_IDLE_MS = 300;
export const POLL_HIDDEN_MS = 1000;
export const POLL_FAST_WINDOW_MS = 1500;
export const POLL_WARM_WINDOW_MS = 4000;

/**
 * Espera hasta la siguiente llamada a pty_poll_output. Rapido (~30 ms) mientras haya salida
 * reciente o la persona acaba de teclear, retrocede a ~100 ms y luego a ~300 ms en reposo.
 * Con la vista oculta basta un sondeo lento: nadie lo ve y ahorra llamadas al puente.
 * `lastActivityAt` es el instante (ms) de la ultima salida recibida o tecla enviada.
 */
export function computePollDelay({ now, lastActivityAt = 0, hidden = false }) {
  if (hidden) return POLL_HIDDEN_MS;
  const idleFor = Math.max(0, now - (lastActivityAt || 0));
  if (idleFor < POLL_FAST_WINDOW_MS) return POLL_FAST_MS;
  if (idleFor < POLL_WARM_WINDOW_MS) return POLL_WARM_MS;
  return POLL_IDLE_MS;
}

// ---------------------------------------------------------------------------
// Memoria de sesiones (solo en memoria del modulo, no en disco)
// ---------------------------------------------------------------------------

// Respaldo para la carpeta de cada sesion cuando el backend no la devuelve en la lista de
// sesiones (pty_get_matrix_status). Se recuerda mientras dure la pagina.
const sessionMemory = new Map();

export function rememberPaneSession(paneId, { sessionId, cwd = null, kind = 'cli' }) {
  if (!paneId || !sessionId) return;
  sessionMemory.set(paneId, { sessionId, cwd, kind });
}

export function recallPaneSession(paneId, sessionId) {
  const entry = sessionMemory.get(paneId);
  if (!entry || !sessionId || entry.sessionId !== sessionId) return null;
  return entry;
}

export function forgetPaneSession(paneId) {
  sessionMemory.delete(paneId);
}

export function resetPaneSessionMemory() {
  sessionMemory.clear();
}

// ---------------------------------------------------------------------------
// Errores del backend -> mensaje + accion sugerida
// ---------------------------------------------------------------------------

function listOrEmpty(value) {
  return Array.isArray(value) ? value.filter((v) => typeof v === 'string' && v) : [];
}

/**
 * Convierte la respuesta de error del puente en algo mostrable. Devuelve:
 * { code, tone, title, message, hint, cliId, manualCommand, mcpConfig, installCommand, actions }
 * `actions` es una lista de {kind, cliId?}: 'install' | 'configure_path' | 'show_manual'.
 * Nunca usa la palabra generica "degradado" como causa: cada codigo dice que falla y que hacer.
 */
export function describeTerminalError(res, context = {}) {
  const data = res && typeof res === 'object' ? res : {};
  const code = String(data.code || 'UNKNOWN_ERROR');
  const cliId = data.cli_id || data.cli_profile || context.cliId || null;
  const cliName = context.cliName || data.display_name || cliId || 'el CLI';
  const backendMessage = typeof data.message === 'string' && data.message
    ? data.message
    : (typeof data.error === 'string' ? data.error : '');
  const maxPanes = Number(data.max_panes ?? context.maxPanes) || DEFAULT_MAX_PANES;

  const base = {
    code,
    tone: 'error',
    title: 'No se pudo completar la acción',
    message: backendMessage,
    hint: '',
    cliId,
    manualCommand: null,
    mcpConfig: null,
    installCommand: null,
    actions: []
  };

  switch (code) {
    case 'CLI_BINARY_NOT_FOUND': {
      const installAvailable = Boolean(data.install_available ?? context.installAvailable);
      const missing = listOrEmpty(data.install_missing_requirements);
      const actions = [];
      if (installAvailable && cliId) actions.push({ kind: 'install', cliId });
      if (cliId) actions.push({ kind: 'configure_path', cliId });
      return {
        ...base,
        title: `No se encontró ${cliName}`,
        message: backendMessage || `El ejecutable de ${cliName} no está en el PATH ni en las rutas conocidas.`,
        hint: installAvailable
          ? 'Puedes instalarlo desde la lista de CLIs (te mostraremos el comando exacto antes de ejecutarlo) o indicar la ruta de su binario.'
          : (missing.length
            ? `No se puede instalar desde aquí: faltan herramientas previas (${missing.join(', ')}). Instálalas o indica la ruta del binario.`
            : 'Indica la ruta de su binario en la lista de CLIs o instálalo manualmente.'),
        actions
      };
    }
    case 'CLI_PROFILE_UNKNOWN': {
      const known = listOrEmpty(data.known_profiles);
      return {
        ...base,
        title: 'Perfil de CLI no reconocido',
        message: backendMessage || 'Ese CLI no está en el catálogo.',
        hint: known.length
          ? `Elige uno de los CLIs del catálogo: ${known.join(', ')}.`
          : 'Elige uno de los CLIs de la lista del catálogo.'
      };
    }
    case 'PANE_LIMIT_REACHED': {
      const reason = String(data.reason || context.reason || '');
      let title = 'Límite de terminales alcanzado';
      let hint = 'Cierra una terminal o sube el máximo de paneles.';
      if (reason === 'pane_out_of_range') {
        title = 'Panel fuera de rango';
        hint = `Los paneles válidos van de pane_1 a pane_${maxPanes}. Sube el máximo de paneles o usa uno libre.`;
      } else if (reason === 'max_panes') {
        title = `Ya hay ${maxPanes} terminales abiertas`;
        hint = 'Cierra una terminal o sube el máximo de paneles (hasta 6; lo óptimo son 4).';
      } else if (reason === 'low_memory') {
        title = 'Memoria disponible insuficiente';
        hint = 'No se abrió otra terminal para no agotar la memoria. Cierra una terminal o libera memoria y reintenta.';
      }
      return { ...base, tone: 'warning', title, message: backendMessage, hint };
    }
    case 'INVALID_CWD':
      return {
        ...base,
        title: 'Carpeta de trabajo no válida',
        message: backendMessage || 'La carpeta indicada no se puede usar.',
        hint: 'Escribe una ruta absoluta de una carpeta que exista, o deja el campo vacío para usar la del proyecto activo.'
      };
    case 'PREREQUISITE_MISSING': {
      const missing = listOrEmpty(data.missing).length ? listOrEmpty(data.missing) : listOrEmpty(data.install_missing_requirements);
      return {
        ...base,
        tone: 'warning',
        title: 'Faltan herramientas previas',
        message: backendMessage || `Falta: ${missing.join(', ')}.`,
        hint: missing.length
          ? `Instala primero: ${missing.join(', ')}. Esta interfaz no instala herramientas previas.`
          : 'Instala las herramientas previas indicadas; esta interfaz no las instala.',
        installCommand: data.command || null
      };
    }
    case 'TERMINAL_PLATFORM_UNSUPPORTED':
      return {
        ...base,
        tone: 'info',
        title: 'Las terminales integradas aún no están disponibles en Windows',
        message: backendMessage || 'Falta el conector ConPTY de Windows, así que esta ventana no puede abrir terminales por ahora.',
        hint: 'Ejecuta tu CLI en una terminal propia y conéctalo al servidor MCP con el comando manual.',
        manualCommand: data.manual_command || DEFAULT_MANUAL_COMMAND,
        mcpConfig: data.mcp_config || DEFAULT_MCP_CONFIG,
        installCommand: data.install_command || null,
        actions: [{ kind: 'show_manual' }]
      };
    case 'TERMINAL_ADAPTER_DEGRADED':
    case 'PTY_UNAVAILABLE':
      return {
        ...base,
        tone: 'warning',
        title: 'El conector de terminales no está disponible',
        message: backendMessage || 'El backend PTY no responde o está en circuito abierto.',
        hint: 'Reintenta con «Actualizar estado». Mientras tanto puedes ejecutar el CLI en una terminal propia y conectarlo con el comando manual.',
        manualCommand: data.manual_command || DEFAULT_MANUAL_COMMAND,
        mcpConfig: data.mcp_config || DEFAULT_MCP_CONFIG,
        actions: [{ kind: 'show_manual' }]
      };
    case 'INSTALL_TOKEN_EXPIRED':
      return {
        ...base,
        tone: 'warning',
        title: 'La confirmación de instalación venció',
        message: backendMessage || 'Pasaron más de 2 minutos desde que se preparó la instalación.',
        hint: 'Pulsa «Instalar» otra vez para revisar el comando y confirmarlo de nuevo.'
      };
    case 'INSTALL_TOKEN_INVALID':
      return {
        ...base,
        title: 'Confirmación de instalación no válida',
        message: backendMessage || 'El token ya se usó o no corresponde a esta instalación.',
        hint: 'Pulsa «Instalar» otra vez para preparar una nueva confirmación.'
      };
    case 'INSTALL_NOT_ALLOWED':
      return {
        ...base,
        title: 'Este CLI no se puede instalar desde la interfaz',
        message: backendMessage,
        hint: 'Solo se instalan comandos verificados del catálogo del proyecto. Instálalo manualmente.'
      };
    case 'INSTALL_NOT_AVAILABLE':
      return {
        ...base,
        tone: 'warning',
        title: 'No hay instalación disponible para este sistema',
        message: backendMessage,
        hint: 'Instálalo manualmente y luego indica la ruta de su binario.'
      };
    case 'ALREADY_INSTALLED':
      return {
        ...base,
        tone: 'info',
        title: `${cliName} ya está instalado`,
        message: backendMessage,
        hint: 'Actualiza el estado para ver su ruta y ábrelo desde la lista.'
      };
    case 'PANE_ALREADY_ACTIVE':
      return {
        ...base,
        tone: 'warning',
        title: 'Ese panel ya tiene una sesión viva',
        message: backendMessage,
        hint: 'Actualiza el estado o ciérralo desde la lista de terminales activas.'
      };
    case 'INVALID_PATH':
    case 'PATH_NOT_FOUND':
    case 'PATH_NOT_EXECUTABLE':
      return {
        ...base,
        title: 'Ruta del binario no válida',
        message: backendMessage,
        hint: 'Indica la ruta absoluta de un archivo ejecutable existente.'
      };
    case 'COMMAND_NOT_ALLOWED':
      return {
        ...base,
        title: 'Comando no permitido',
        message: backendMessage,
        hint: 'Los CLIs del catálogo se lanzan con su comando guardado; no admiten comandos propios.'
      };
    case 'INVALID_ARGUMENT':
    case 'INVALID_PANE_ID':
      return { ...base, title: 'Argumento no válido', message: backendMessage };
    case 'CONFIRMATION_DECLINED':
      return {
        ...base,
        tone: 'info',
        title: 'Cancelaste la confirmación',
        message: backendMessage || 'El diálogo de confirmación se rechazó, así que no se hizo ningún cambio.',
        hint: 'Vuelve a intentarlo cuando quieras continuar y acepta el diálogo de confirmación.'
      };
    case 'PTY_SPAWN_FAILED':
      return {
        ...base,
        title: 'No se pudo iniciar el proceso',
        message: backendMessage || 'El sistema operativo no pudo crear la sesión del terminal.',
        hint: 'Revisa la carpeta de trabajo y la ruta del binario, y reintenta.'
      };
    default:
      return {
        ...base,
        message: backendMessage || 'El backend no devolvió detalles.',
        hint: ''
      };
  }
}

/** Por que un CLI no tiene boton Instalar. Devuelve texto o null si SI se puede instalar. */
export function describeInstallBlock(cli) {
  if (!cli || cli.installed || cli.kind === 'shell') return null;
  const install = cli.install || {};
  if (cli.verified === false || cli.source === 'user') {
    return 'Viene del catálogo del usuario y no está verificado: no se instala desde la interfaz.';
  }
  if (install.verified === false && install.command) {
    return 'El comando de instalación no está verificado: instálalo manualmente.';
  }
  if (install.available) return null;
  const missing = listOrEmpty(install.missing_requirements);
  if (missing.length) {
    return `Faltan herramientas previas: ${missing.join(', ')}. Instálalas primero; esta interfaz no las instala.`;
  }
  return 'No hay un método de instalación verificado para este sistema: instálalo manualmente.';
}

export function canOfferInstall(cli) {
  return Boolean(
    cli
    && !cli.installed
    && cli.kind !== 'shell'
    && cli.verified !== false
    && cli.install?.available
    && cli.install?.verified !== false
  );
}
