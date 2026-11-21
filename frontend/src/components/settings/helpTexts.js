/**
 * Textos breves (<= 140 caracteres) para los iconos ⓘ y guía veraz de la sección
 * «Terminales y relevo» en Ajustes.
 */

export const HINT_TEXTS = {
  autoRelay: 'Deja avisos de tareas asignadas en la bandeja de este agente.',
  deliveryDirect: 'Los mensajes llegan escritos en la terminal cuando el agente queda libre.'
};

export const HELP_SECTIONS = [
  {
    id: 'ayuda-auto-relay',
    title: 'Auto-Relay',
    body: 'Se activa por terminal y se recuerda en este proyecto. Deja un aviso en la bandeja del agente cuando le asignan una tarjeta del tablero. El Auto-Relay no escribe en su terminal: el agente lo lee con inbox_next o inbox_wait cuando se lo pidas.'
  },
  {
    id: 'ayuda-entrega-directa',
    title: 'Entrega directa',
    body: 'Cuando el agente de una terminal queda libre, MCP2P escribe el mensaje en su terminal como un prompt, con una cabecera [MCP2P …] que indica quién lo envía. Solo con CLIs medidos, nunca a terminales con permisos saltados, nunca mientras escribes tú. El mensaje queda siempre guardado y auditado en la bandeja.',
    slotId: 'ajustes-entrega'
  },
  {
    id: 'ayuda-medicion',
    title: 'Medición de CLIs',
    body: 'Comprueba en este equipo si cada CLI avisa de sus turnos y acepta mensajes pegados. Si sale bien, activa la entrega directa para ese CLI. Gasta unos pocos tokens por CLI.',
    slotId: 'ajustes-medicion'
  },
  {
    id: 'ayuda-roles',
    title: 'Roles',
    body: 'El rol lo eliges tú y decide qué tipos de mensaje puede enviar cada agente.'
  },
  {
    id: 'ayuda-mandato',
    title: 'Mandato',
    body: 'El mandato es una instrucción para el agente, no un candado: MCP2P solo lo refuerza donde el CLI tiene una opción de solo lectura.'
  },
  {
    id: 'ayuda-otros-equipos',
    title: 'Otros equipos',
    body: 'En esta versión aún no se reciben mensajes de otros equipos; cuando lleguen (2.0.3) pasarán por la zona de Permisos según la política de cada nodo.',
    slotId: 'ajustes-nodos'
  },
  {
    id: 'ayuda-sesiones',
    title: 'Sesiones',
    body: 'MCP2P recuerda qué agentes tenías abiertos (CLI, carpeta, rol, mandato e interruptores) para relanzarlos y reanudar su conversación cuando el CLI lo soporta, sin guardar nunca la salida de la terminal.',
    slotId: 'ajustes-sesiones'
  },
  {
    id: 'ayuda-confirmaciones',
    title: 'Confirmaciones',
    body: 'Las acciones de bajo riesgo usan un diálogo propio donde puedes elegir «No volver a preguntar». Las acciones peligrosas o con permisos saltados mantienen siempre la confirmación nativa.',
    slotId: 'ajustes-confirmaciones'
  }
];
