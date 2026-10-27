# Especificación de UX/UI y Guía de Usuario Integrada
## Principios Heurísticos, Ergonomía Cognitiva y Manual de Experiencia de Usuario

> **Proyecto**: MCP2P Desktop  
> **Fundamentos Teóricos**: 10 Heurísticas de Usabilidad de Jakob Nielsen | Ley de Hick  
> **Enfoque de Diseño**: Densidad informativa equilibrada, tipografía técnica clara y modo visual dual (Claro / Oscuro)  

---

## 1. Fundamentos de Diseño y Ergonomía

La interfaz de MCP2P está concebida para desarrolladores e ingenieros que trabajan conjuntamente con agentes de inteligencia artificial. El objetivo principal es **reducir al mínimo la sobrecarga cognitiva**, permitiendo que el desarrollador comprenda de un vistazo el estado de todos los procesos sin distracciones visuales.

### 1.1. Aplicación Práctica de la Ley de Hick
La **Ley de Hick** postula que el tiempo que tarda una persona en tomar una decisión aumenta logarítmicamente con el número de alternativas disponibles:

$$T = b \cdot \log_2(n + 1)$$

Para respetar este principio en MCP2P:
1. **Regla de 3 Acciones Primarias**: En la pantalla principal solo se muestran tres acciones visibles en la barra superior:
   * **Nueva Tarea / Tarjeta**
   * **Filtrar por Agente / Estado**
   * **Guía de Usuario / Ayuda (?)**
2. **Menús de Contexto Focalizados**: Al hacer clic en una tarjeta Kanban, sólo se despliegan las transiciones de estado legales para el rol actual (ej. un desarrollador ve *Asignar*, *Aprobar*, *Rechazar*; un agente ve *Reclamar Lease*, *Finalizar*).
3. **Divulgación Progresiva (Progressive Disclosure)**: Los datos de bajo nivel (hashes SHA-256 de commits, identificadores UUID de nodos, payloads JSON de eventos) están ocultos en paneles colapsables tipo *accordion* que se abren únicamente cuando el usuario lo solicita.

---

## 2. Implementación de las 10 Heurísticas de Usabilidad de Nielsen

| # | Heurística de Nielsen | Implementación Concreta en MCP2P |
| :-: | :--- | :--- |
| **1** | **Visibilidad del estado del sistema** | Barra de estado inferior (*Status Bar*) con indicadores de pulso: estado del servidor MCP (🟢 En línea / 🔴 Desconectado), agentes activos, y tareas con lease en curso con contador regresivo en minutos. |
| **2** | **Coincidencia entre sistema y mundo real** | Uso del tablero Kanban con columnas clásicas (*Por Hacer*, *En Proceso*, *En Revisión*, *Consolidado*), icono de candado visual para tareas bloqueadas por lease, y notificaciones directas tipo chat para la comunicación A2A. |
| **3** | **Control y libertad del usuario** | El usuario humano siempre tiene control superior (HITL): puede liberar un lease atascado de un agente con un solo clic, pausar agentes o devolver una tarjeta a *Backlog* sin fricción. |
| **4** | **Consistencia y estándares** | Los estados de las tareas, los colores de insignias (*badges*) y los botones interactivos siguen una convención idéntica en toda la app. El botón primario siempre está ubicado a la derecha y los destructivos a la izquierda. |
| **5** | **Prevención de errores** | Los campos de entrada tienen validación en tiempo real. No se permite transicionar una tarea a *En Revisión* sin al menos una referencia de commit o nota explicativa. |
| **6** | **Reconocimiento antes que recuerdo** | Las tarjetas muestran directamente el avatar del agente asignado, el tiempo restante de lease y etiquetas clave. El usuario no necesita recordar qué agente estaba asignado a qué tarjeta. |
| **7** | **Flexibilidad y eficiencia de uso** | Atajos de teclado intuitivos (`Ctrl+N` para nueva tarea, `Ctrl+K` para buscador rápido, `Ctrl+D` para alternar modo claro/oscuro, `F1` para abrir la Guía de Usuario). |
| **8** | **Diseño estético y minimalista** | Interfaz sin adornos ni degradados excesivos. Superficies monocromáticas limpias, bordes de 1 píxel sutiles y contraste de texto conforme a la norma WCAG AAA. |
| **9** | **Ayudar a reconocer y subsanar errores** | Los mensajes de error explican la causa técnica en lenguaje claro con una sugerencia de acción inmediata (ej. *"Conflicto de Lease: La tarjeta está bloqueada por Agente Alpha hasta las 20:15. [Forzar Liberación]"*). |
| **10** | **Ayuda y documentación** | **Panel de Guía de Usuario Integrado** accesible en cualquier momento desde un botón superior o mediante atajo de teclado, con tutorial interactivo y preguntas frecuentes. |

---

## 3. Sistema Visual y Tokens de Diseño

### 3.1. Tipografía
* **Interfaz de Usuario y Títulos**: **Inter** (o fuente del sistema `-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`).
  * Excelente legibilidad a tamaños pequeños (12px - 14px) en pantallas de alta y baja densidad.
* **Código, Hashes y Logs**: **JetBrains Mono** / **Roboto Mono** / `ui-monospace`.
  * Caracteres diferenciados claramente (`0` vs `O`, `1` vs `l`), óptimo para trazabilidad de commits y deltas.

### 3.2. Paleta de Colores y Tokens CSS

#### Tema Oscuro (Dark Theme - Predeterminado)
```css
:root[data-theme="dark"] {
  --bg-primary: #121316;       /* Fondo de la ventana */
  --bg-secondary: #1a1b20;     /* Paneles y columnas del Kanban */
  --bg-tertiary: #24262c;      /* Tarjetas individuales y modales */
  --bg-hover: #2e3038;         /* Estados hover */
  
  --border-subtle: #2b2d35;    /* Líneas divisorias y bordes de tarjeta */
  --border-focus: #4f75ff;     /* Borde activo */
  
  --text-primary: #f1f2f6;     /* Texto de alto contraste */
  --text-secondary: #9ea3ae;   /* Metadatos y subtítulos */
  --text-muted: #676c79;       /* Placeholders y marcas temporales */
  
  --accent-primary: #4f75ff;   /* Azul índigo para acciones primarias */
  --accent-success: #10b981;   /* Esmeralda para tareas consolidadas */
  --accent-warning: #f59e0b;   /* Ámbar para leases y atención */
  --accent-danger: #ef4444;    /* Rojo para advertencias y bloqueos */
}
```

#### Tema Claro (Light Theme)
```css
:root[data-theme="light"] {
  --bg-primary: #f8f9fa;
  --bg-secondary: #ffffff;
  --bg-tertiary: #f1f3f5;
  --bg-hover: #e9ecef;
  
  --border-subtle: #e2e4e9;
  --border-focus: #3b82f6;
  
  --text-primary: #18191c;
  --text-secondary: #5a5f6d;
  --text-muted: #8b91a0;
  
  --accent-primary: #3b82f6;
  --accent-success: #059669;
  --accent-warning: #d97706;
  --accent-danger: #dc2626;
}
```

---

## 4. Guía de Usuario Integrada (Componente en la UI)

Para garantizar la **Heurística 10 de Nielsen**, la aplicación cuenta con un módulo de guía integrado directamente en la interfaz.

### 4.1. Ubicación y Mecánica de Interacción
* **Acceso Permanente**: Un botón discreto `[ ? Guía ]` en la esquina superior derecha y el atajo `F1`.
* **Presentación**: Un panel lateral deslizable (*Drawer*) que no interrumpe el trabajo sobre el tablero Kanban, permitiendo consultar instrucciones mientras se manipula la aplicación.
* **Estructura del Contenido de la Guía**:
  1. **Inicio Rápido**: Cómo crear una tarea, cómo un agente la toma mediante MCP y cómo liberarla.
  2. **Concepto de Leases**: Explicación sencilla de por qué las tareas se bloquean temporalmente para evitar que dos agentes choquen.
  3. **Comunicación Agente a Agente (A2A)**: Cómo leer el monitor de eventos y qué significan las alertas prioritarias.
  4. **Atajos de Teclado**: Tabla de referencia para navegación ágil.
  5. **Resolución de Problemas**: Qué hacer si una tarea queda bloqueada o si se pierde la conexión local.
