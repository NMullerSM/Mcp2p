# Changelog

Este archivo cuenta qué cambia en MCP2P para quien lo usa: funciones nuevas, mejoras, correcciones y seguridad.
No describe cómo se implementó cada cosa. Para ver los commits exactos de una versión, usa el enlace de comparación
junto a su título (también están al final del archivo).

Formato: [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/). Versionado: [SemVer](https://semver.org/lang/es/).
La versión vive en `src/app/version.py` (se sincroniza con `python scripts/sync_version.py`); la nota de cada release
en GitHub se toma de la sección de este archivo que coincide con esa versión.

## [Sin publicar]

### Añadido
- Antigravity (`agy`): MCP2P ya no toca su archivo de configuración global sin permiso. Antes de registrarse muestra
  un diálogo del sistema con el archivo y la entrada exactos, recuerda tu respuesta y puedes quitar el registro cuando
  quieras. Si dices que no, el panel se abre igual y ofrece el botón «Registrar MCP2P en Antigravity».
- La opción `--hub-proxy-only` conecta un CLI externo al Hub de MCP2P sin abrir tu base de datos; también funciona con
  la aplicación empaquetada (`mcp2p --mcp-server --hub-proxy-only`). Abierto fuera de un panel, avisa de que hay que
  abrir el CLI desde MCP2P en lugar de usar datos que no son del panel.
- Auto-Relay por terminal: cada terminal tiene su propio interruptor en «Terminales activas», empieza apagado, pide
  confirmación al encenderlo y MCP2P lo recuerda para esa terminal en ese proyecto aunque cierres la aplicación.
- Bandeja de mensajes entre agentes que no se pierde al cerrar MCP2P. Los agentes la usan con `send_message`,
  `inbox_next`, `inbox_wait` (espera hasta que llega algo), `inbox_ack` e `inbox_reply`. Cada mensaje llega con su
  remitente verificado por MCP2P y separado del texto, que el agente recibe como dato y no como orden tuya.
- Los mensajes que MCP2P retiene para ti (una conversación automática entre agentes que ya dio su vuelta, o un mensaje
  que el agente pidió confirmar) quedan guardados en tu bandeja: no se entregan solos. La pantalla para revisarlos y
  entregarlos o descartarlos, siempre tras ver el texto exacto en un diálogo del sistema, llega en una próxima versión.
- Parada del relevo: un interruptor general detiene la entrega de mensajes entre agentes sin perder ninguno, y un
  agente puede pausar su propia bandeja con `relay_pause_self`. Solo tú puedes reanudarlos, con confirmación.

### Mejorado
- Cada terminal conserva su identidad al cerrarla y volver a abrirla en el mismo proyecto, también tras reiniciar
  MCP2P, así que las tarjetas asignadas a ese agente siguen siendo suyas.
- Al cambiar de proyecto, las terminales del proyecto anterior siguen abiertas pero ocultas, y el panel lateral
  indica cuántas hay; al volver aparecen tal como estaban.
- La conexión de los CLI externos con el Hub aguanta esperas largas del agente (hasta unos 5 minutos), se reconecta
  sola si el Hub se reinicia en la misma dirección y avisa al instante cuando MCP2P está cerrado, en lugar de quedarse
  colgada.
- El registro de MCP2P en Antigravity se escribe sin riesgo de dejar el archivo a medias, guarda una copia del original
  la primera vez, no sigue enlaces simbólicos y respeta una entrada `mcp2p` que hayas puesto a mano.
- En la aplicación empaquetada, un CLI cuya integración MCP aún no está comprobada ya no se anuncia como «MCP Hub»:
  la etiqueta del panel dice «MCP sin verificar».
- `send_pane_message` y `await_next_assignment` siguen funcionando durante esta versión, usan la nueva bandeja y avisan
  en su respuesta de que serán retiradas; conviene pasar a `send_message` e `inbox_next`.

### Seguridad
- Cada terminal pertenece al proyecto en el que se abrió: un agente solo ve las terminales de su proyecto y solo
  puede enviar mensajes o recibir asignaciones dentro de él. Si un agente consulta o modifica otro proyecto, la
  acción queda registrada y la respuesta lo avisa; en una versión próxima se bloqueará.
- El código de sesión de una terminal solo sirve desde los procesos de esa terminal. Si otro programa de tu equipo lo
  copia e intenta enviar o leer mensajes como si fuera ese agente, MCP2P lo rechaza; las demás herramientas lo
  registran y lo avisan en la respuesta.
- Cada mensaje entre agentes va firmado por tu equipo con una clave propia, guardada en el llavero del sistema (o en
  un archivo cifrado si no hay llavero, con un aviso). Un mensaje alterado después de enviarse llega marcado como no
  verificado y un mensaje repetido se rechaza.
- Dos agentes ya no pueden mantener una conversación automática sin fin: tras una respuesta automática, el siguiente
  mensaje de la misma conversación espera en tu bandeja. Cambiar de tarjeta no lo evita, y cuando escribes tú en esa
  terminal empieza una conversación nueva.
- Registro de auditoría encadenado de todos los mensajes entre agentes (enviado, entregado, confirmado, retenido,
  rechazado) que permite detectar si alguien editó o borró una entrada. El texto de los mensajes se borra a los
  30 días; el registro de que existieron se conserva.

## [2.0.1] - 2026-11-17 · [commits](https://github.com/NMullerSM/Mcp2p/compare/v2.0.0...v2.0.1)

### Añadido
- Terminales integradas en **Windows**: los paneles de Swarm Terminal Matrix ahora funcionan también allí (Claude,
  Gemini, Codex, OpenCode, Antigravity o una shell), con instalación guiada de los CLI que falten.

### Mejorado
- El relevo entre agentes es más seguro: el aviso de Auto-Relay y los mensajes entre paneles ya no se escriben en la
  terminal de otro agente; el agente destino los recibe en su bandeja y los recoge cuando quiere. El interruptor de
  Auto-Relay pide confirmación al activarse y vuelve a empezar apagado.
- Los mensajes entre paneles tienen un tamaño máximo (8 KiB) y no salen a otros colaboradores de la red P2P.
- La lista de paneles que ve un agente ya no expone datos internos del proceso ni si estás escribiendo.

### Corregido
- **Actualizar** en Windows ya muestra el progreso real (descarga, verificación, instalación) y, si algo falla, la
  causa con opciones para reintentar, abrir la página de descarga o ver el registro. Antes el botón parecía no hacer
  nada. Al terminar, la app se cierra, se instala y se vuelve a abrir sola; la versión portable se reemplaza sin
  perder tus proyectos.
- Un servidor MCP configurado para usar el Hub ya no arranca en silencio como servidor aislado si falta su
  configuración: se detiene y explica el motivo.

## [2.0.0] - 2026-11-16 · [commits](https://github.com/NMullerSM/Mcp2p/compare/v1.1.0...v2.0.0)

### Añadido
- **Swarm Terminal Matrix**: hasta 6 terminales de agentes dentro de la app, con terminal real, tamaño ajustable,
  pausa, reanudación y cierre limpio sin procesos huérfanos. El diseño se adapta solo al número de paneles.
- **Catálogo de CLIs de agentes** (Claude Code, Gemini CLI, Codex, OpenCode, Antigravity y shell): elige CLI y carpeta,
  la app encuentra el binario o ofrece instalarlo mostrando el comando exacto y pidiendo confirmación.
- **Hub MCP embebido**: cada terminal se conecta al Hub de la app con una credencial propia, y los agentes pueden
  coordinarse con herramientas como listar paneles, enviar mensajes y esperar la siguiente asignación.
- **Panel lateral por vista**: Kanban, Cambios, Aprobaciones, Colaboradores, OKF y Terminales llevan sus
  herramientas a una pestaña lateral derecha, y la pantalla queda para el contenido.
- **Firma humana** protegida con frase: la clave se cifra, se desbloquea de forma explícita y se bloquea de nuevo
  desde Configuración.
- **Exportar e importar proyectos** desde la línea de comandos, con copia consistente y rutas reescritas.
- Red P2P con varios pares a la vez y conexión directa 1:1; búsqueda y filtros por etiquetas en OKF, con frescura de
  los documentos y verificación de firmas de las decisiones.
- Un puente HTTP para que Antigravity use el Hub de MCP2P sin configuración manual.

### Mejorado
- Núcleo reorganizado en piezas independientes (puertos y adaptadores) para poder añadir módulos sin tocar el resto.
- Base de datos más robusta con varias conexiones simultáneas, y una única regla de aprobación humana en todo el
  sistema.
- Barra superior más suave y navegable por iconos, con proyectos en un selector compacto.

### Corregido
- La app volvía a abrirse sin proyectos ni token de GitHub en algunos equipos por una política de seguridad demasiado
  estricta; ya carga todo con normalidad.
- Los atajos de teclado globales ya no interfieren con lo que escribes en una terminal.
- Cierre de varios fallos de concurrencia al abrir paneles a la vez y al mover mensajes entre agentes.

### Seguridad
- El Hub exige credencial por panel y rechaza las llamadas sin ella; la identidad del agente la fija el servidor.
- Instalar un CLI, fijar su ruta o lanzar un comando propio requiere confirmación nativa de la app.
- Las acciones de apertura de terminales y del puente con la interfaz quedan acotadas a lo que la app necesita.

## [1.1.0] - 2026-11-14 · [commits](https://github.com/NMullerSM/Mcp2p/compare/v1.0.8...v1.1.0)

### Añadido
- **Solicitudes de acceso a proyectos**: quien quiere unirse a un proyecto envía una solicitud y el propietario la
  aprueba o rechaza dentro de la app. Se muestra el estado pendiente, con reintento, y los bloqueos temporales tras
  rechazos repetidos.
- Conexión directa entre pares por WebRTC, con un servidor de señalización opcional configurable en Ajustes.
- El propietario puede pegar el código de una solicitud recibida por otro canal.

### Corregido
- La actualización de la versión portable conserva tu base de datos y tus espacios de trabajo.

## [1.0.8] - 2026-11-11 · [commits](https://github.com/NMullerSM/Mcp2p/compare/v1.0.5...v1.0.8)

### Corregido
- Las conexiones seguras a GitHub funcionaban mal en el programa instalado; ahora usa los certificados correctos.
- El servidor MCP va incluido en el programa instalado y se corrigieron detalles de la interfaz.
- Sincronización de versiones en la comprobación automática de cada publicación.

## [1.0.5] - 2026-11-10 · [commits](https://github.com/NMullerSM/Mcp2p/compare/v1.0.3...v1.0.5)

### Añadido
- **Tribunal de honor**: las disputas se resuelven con un jurado de agentes y personas, votación a ciegas, quórum
  humano, plazo de gracia y consenso ponderado, con veredictos firmados.
- Nueva identidad visual MCP²P: logo, iconos y favicons.

### Corregido
- La app ya no se cierra al abrirse en Windows por un problema con el icono.
- Se reduce el parpadeo de ventanas de consola en Windows.

## [1.0.3] - 2026-11-09 · [commits](https://github.com/NMullerSM/Mcp2p/compare/v1.0.2...v1.0.3)

### Seguridad
- La conexión P2P en red local solo acepta mensajes firmados por colaboradores aprobados y escucha por defecto solo en
  tu equipo (se puede abrir a la red local con `MCP2P_LAN_BIND_HOST`).
- Límite de tamaño en los mensajes recibidos y validación de nombres de tarjetas, proyectos y elementos para evitar
  accesos a rutas fuera de lugar.

### Corregido
- Dos agentes ya no pueden tomar la misma tarjeta Kanban a la vez.
- Aprobar un pull request ya no lo da por fusionado si la fusión real falló; se muestra el error.
- Crear una tarea sin identificador ya no bloquea el tablero del proyecto.
- Varios errores que ocultaban fallos al sincronizar, crear proyectos o aprobar colaboradores.
- El aviso de actualización aparecía siempre por una versión mal sincronizada.

### Mejorado
- La interfaz deja de parpadear tanto al sincronizar.

## [1.0.2] - 2026-11-06 · [commits](https://github.com/NMullerSM/Mcp2p/compare/v1.0.1...v1.0.2)

### Corregido
- La interfaz no cargaba en el programa empaquetado de Windows.

## [1.0.1] - 2026-11-06 · [commits](https://github.com/NMullerSM/Mcp2p/compare/v1.0.0...v1.0.1)

### Añadido
- Actualización directa para Linux, sin descargar e instalar a mano.

## [1.0.0] - 2026-11-06 · [commits](https://github.com/NMullerSM/Mcp2p/commits/v1.0.0)

### Añadido
- Primera versión publicada: instalador para Windows, paquete para Linux y actualizador automático.

[Sin publicar]: https://github.com/NMullerSM/Mcp2p/compare/v2.0.1...develop
[2.0.1]: https://github.com/NMullerSM/Mcp2p/compare/v2.0.0...v2.0.1
[2.0.0]: https://github.com/NMullerSM/Mcp2p/compare/v1.1.0...v2.0.0
[1.1.0]: https://github.com/NMullerSM/Mcp2p/compare/v1.0.8...v1.1.0
[1.0.8]: https://github.com/NMullerSM/Mcp2p/compare/v1.0.5...v1.0.8
[1.0.5]: https://github.com/NMullerSM/Mcp2p/compare/v1.0.3...v1.0.5
[1.0.3]: https://github.com/NMullerSM/Mcp2p/compare/v1.0.2...v1.0.3
[1.0.2]: https://github.com/NMullerSM/Mcp2p/compare/v1.0.1...v1.0.2
[1.0.1]: https://github.com/NMullerSM/Mcp2p/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/NMullerSM/Mcp2p/commits/v1.0.0
