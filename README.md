<p align="center">
  <img src="brand/png/logo-mcp2p-dark-800w.png" alt="MCP²P Logo" width="500" />
</p>

# MCP²P: Plataforma de Colaboración Agéntica y Gobernanza OKF

> Ver [`CHANGELOG.md`](CHANGELOG.md) para el historial de versiones.

> **Plataforma de Escritorio Local-First** construida con **PyWebView**, **React + Vite**, **Model Context Protocol (MCP)** y persistencia determinista **SQLite (WAL)** para la orquestación, concurrencia y desarrollo colaborativo entre humanos y agentes de Inteligencia Artificial (Claude, Gemini, Antigravity, Cursor, etc.).

---

## 📌 Estado del Sistema y Capacidades Clave

* **Servidor MCP Local (25 Herramientas Deterministas a 0 Tokens)**: Expone herramientas deterministas para gestión de proyectos, leases de Kanban, worktrees aislados por tarea, micro-scripts de gobernanza OKF, canal dialéctico A2A, descubrimiento y arbitraje del Tribunal de Honor Borda con jurado dinámico (10 vectores focales estocásticos), prompts clean-room, sincronización de memoria huérfana e introspección de runtime.
* **Resiliencia Bidireccional de Esquema**: Soporte nativo para convenciones `camelCase` / `snake_case`, payloads estructurados (`dict`/`list`) y cadenas JSON, eliminando errores de validación entre agentes heterogéneos.
* **Control Estricto de Concurrencia**: Bloqueo atómico de tarjetas Kanban con TTL dinámico y detección de colisiones de alcance de archivos (`files_scope`) en Git Worktrees.
* **Tribunal de Honor Borda Anti-Sycophancy**: Resolución algorítmica de disputas técnicas y arquitectónicas entre agentes con ponderación imparcial (peso 1.5).
* **HITL Guardrail (Human-In-The-Loop)**: Los agentes inspeccionan código y recomiendan acciones (`RECOMMEND_ACCEPT`, `RECOMMEND_CHANGES`, `RECOMMEND_REJECT`, `RECOMMEND_JURY`), pero únicamente el humano puede firmar y fusionar mediante su clave Ed25519.
* **Compatibilidad Multiplataforma**:
  * 🐧 **Linux**: Ejecución nativa mediante PyWebView (WebKitGTK 4.1 / 4.0).
  * 🪟 **Windows**: Ejecución nativa mediante PyWebView (WebView2).
  * 🍎 **macOS**: Soporte nativo mediante PyWebView (WebKit Cocoa / pyobjc).
* **Red P2P LAN Autenticada**: el listener de notificaciones en tiempo real escucha por defecto solo en `127.0.0.1`
  (sin exposición en la red local). Para activarlo en LAN: `MCP2P_LAN_BIND_HOST=0.0.0.0` (o la IP de la interfaz). Todo
  payload entrante debe venir firmado con Ed25519 por una clave pública que figure como colaborador aprobado del
  proyecto; cualquier otro paquete se rechaza (401/403).
* **Solicitudes de Colaborador P2P**: flujo de unión asíncrono (invitación → solicitud pendiente → aprobación del
  owner → clonado) con identidad por clave pública Ed25519, límite de reintentos con bloqueo progresivo (30 min / 24 h)
  y transporte modular (WebRTC o intercambio manual de código) para incorporar colaboradores sin exponer credenciales.
* **Terminales Integradas en Enjambre (Swarm)**: varios paneles de terminal reales dentro de la propia app (PTY nativo
  en Linux/macOS, ConPTY vía `pywinpty` en Windows ≥ build 17763), cada uno vinculable a una tarjeta del Kanban. Los
  paneles se comunican entre sí y reciben avisos de Auto-Relay mediante una bandeja de mensajes por nodo, sin exponer
  nunca PID, telemetría de tecleo humano ni tokens de sesión a los agentes.

---

## 📂 Estructura del Proyecto

```text
Mcp2p/
├── docs/                                 # Documentación técnica y de arquitectura
│   ├── okf/                              # Gobernanza viva OKF (ADRs, decisiones, requirements, mcps)
│   └── GUIA_DE_USUARIO_Y_UX.md           # Heurísticas de Nielsen y diseño UX
├── frontend/                             # Interfaz de usuario SPA en React + Vite + Tailwind/Lucide
│   ├── src/                              # Componentes visuales conectados de verdad al puente PyWebView
│   │   └── components/terminals/        # Paneles de terminal del Swarm (modelo, registro, controles)
│   └── dist/                             # Bundle estático servido localmente a coste cero de red
├── src/                                  # Backend modular en Python 3.11+
│   ├── app/                              # Lógica de arranque (main.py) y puente bidireccional (api_bridge.py)
│   ├── mcp/                              # Servidor local Model Context Protocol (server.py y tools.py)
│   ├── a2a/                              # Canal dialéctico Agente-a-Agente y motor de juicio Borda
│   ├── p2p/                              # Puerto de transporte P2P (WebRTC / adaptador nulo) para colaboradores
│   ├── runtime/                          # Detección de plataforma de terminal (PTY / ConPTY) por SO real
│   ├── adapters/terminal/                # Conectores de terminal (ConPTY en Windows, PTY en Linux/macOS)
│   └── storage/                          # Base de datos SQLite (WAL) y adaptadores de memoria OKF
├── run.sh                                # Lanzador automático para Linux / macOS (con auto-build de frontend)
├── run.bat                               # Lanzador automático para Windows (con auto-build de frontend)
└── README.md                             # Este archivo
```

---

## 🚀 Inicio Rápido

Tras clonar, activa la guardia de compilación de commits con: `git config core.hooksPath .githooks`.

### Linux / macOS
```bash
chmod +x run.sh
./run.sh
```
El script detectará el entorno virtual `.venv`, compilará el bundle de React si hay cambios en `frontend/src/` y lanzará la aplicación de escritorio.

### Windows
```cmd
run.bat
```

### Servidor MCP para Clientes Agénticos (`mcp.json`)
Para conectar Claude Desktop, Cursor o Antigravity IDE (Linux/macOS, usar la ruta absoluta al intérprete del `.venv`):
```json
{
  "mcpServers": {
    "mcp2p": {
      "command": "/ruta/absoluta/a/Mcp2p/.venv/bin/python",
      "args": ["-m", "src.mcp.server"],
      "env": {
        "PYTHONPATH": "/ruta/absoluta/a/Mcp2p",
        "MCP2P_DB_PATH": "/ruta/absoluta/a/Mcp2p/mcp2p_local.db"
      }
    }
  }
}
```
En Windows, `command` debe apuntar a `C:\\ruta\\absoluta\\a\\Mcp2p\\.venv\\Scripts\\python.exe` (y `PYTHONPATH`/`MCP2P_DB_PATH` con la misma ruta base en Windows).

---

## 📦 Descarga, Releases y Actualización Automática

### Binarios Oficiales

Cada push a `main` ejecuta el pipeline de CI/CD (`.github/workflows/build-and-release.yml`), que compila los paquetes de cada plataforma y los publica como **GitHub Release** oficial en https://github.com/NMullerSM/Mcp2p/releases, junto con un manifiesto **`SHA256SUMS`** de integridad.

| Plataforma | Archivo | Modo de instalación |
|---|---|---|
| 🪟 Windows | `Mcp2p-Setup-vX.Y.Z.exe` | Instalador (Inno Setup) |
| 🪟 Windows | `Mcp2p-Windows-Portable.zip` | Portable, sin instalación |
| 🐧 Linux (Pop!_OS / Ubuntu / Debian) | `mcp2p_X.Y.Z_amd64.deb` | Paquete de sistema (`/opt`, vía `apt`/`dpkg`) |
| 🐧 Linux | `Mcp2p-Linux-Portable.tar.gz` | Portable, en carpeta de usuario |

### Actualización Automática

Al abrir la aplicación, consulta en **segundo plano** la última release de GitHub y compara versiones (**semver**). Si existe una más nueva, muestra una notificación discreta ("Nueva versión disponible") sin interrumpir el uso. Al aceptar, la descarga se realiza en segundo plano con progreso en MB y porcentaje, y el archivo se **verifica criptográficamente contra `SHA256SUMS`** antes de aplicar nada. Si el hash no coincide, la actualización se **rechaza**.

La aplicación detecta sola la plataforma y el modo de instalación:

* **Windows (instalador o portable)**: ejecuta el instalador en modo silencioso (`/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS`), cierra la versión anterior y la reabre automáticamente. Cero pasos manuales.
* **Linux portable (tarball en carpeta de usuario)**: reemplaza en caliente los archivos de la carpeta de la aplicación con el tarball ya verificado y relanza la app. Cero pasos manuales, sin contraseña.
* **Linux `.deb` (instalación de sistema)**: ejecuta `pkexec dpkg -i` con el nuevo paquete y relanza la app. Es la **única excepción con un paso manual** (un clic y contraseña en el diálogo de polkit). Es una limitación de permisos del sistema operativo, no un descuido: escribir en `/opt` y `/usr` requiere privilegios de root, y polkit exige autenticación explícita del usuario para concederlos.

En modo desarrollo (ejecución desde código fuente con `.venv`), "aplicar actualización" equivale a un `git pull` de la rama actual. Este modo no aplica a usuarios finales.

### Datos de Usuario Persistentes

Los datos del usuario **nunca se tocan ni se pierden durante una actualización**, porque viven en un directorio separado del de instalación:

* **Windows**: `%LOCALAPPDATA%\MCP2P\data`
* **Linux**: `~/.local/share/mcp2p/data`

Ese directorio contiene la base de datos SQLite local y la carpeta `workspace/` con los proyectos clonados.

### Publicar una nueva versión

1. Editar solo `src/app/version.py` (fuente única de verdad).
2. Ejecutar `python scripts/sync_version.py` para propagar el número a `pyproject.toml`, `frontend/src/version.js`,
   `frontend/package.json` y `frontend/package-lock.json`.
3. Añadir la entrada correspondiente en [`CHANGELOG.md`](CHANGELOG.md).

El CI falla antes de compilar si alguna copia queda desincronizada (`python scripts/sync_version.py --check`), y lo
mismo valida `tests/test_version_consistency.py` en local.

---

## 🧪 Pruebas Automatizadas

```bash
PYTHONPATH=. .venv/bin/pytest tests/ -v
cd frontend && npm test   # vitest: hooks de React (useProjectPulse)
```
Suite exhaustiva con 778 pruebas unitarias y de integración en backend (algunas se omiten fuera de un host Windows real, p. ej. las de ConPTY) cubriendo persistencia, criptografía Ed25519, colisiones de leases (incluida concurrencia real multi-proceso), solicitudes de colaborador P2P y su límite de reintentos, autenticación del webhook P2P, path traversal, worktrees aislados, bus dialéctico A2A, tribunal de honor dinámico, el relevo del Swarm de terminales, las herramientas MCP y actualización automática multiplataforma. Ver [`CHANGELOG.md`](CHANGELOG.md) para el detalle por versión.
