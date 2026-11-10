# MCP²P Design System & Brand Guidelines

> **Nombre Oficial:** MCP²P  
> **Formato:** `MCP²P` (con el '2' en superíndice geométrico)  
> **Tagline:** `AGENT CONSENSUS & GOVERNANCE`  
> **Concepto Visual:** *Dialectic Node (Pure Emerald & White)*  
> **Estado:** Aprobado para Producción

---

## 1. Misión Visual y Racional de Marca

MCP²P es un entorno determinista local-first de orquestación, concurrencia y desarrollo colaborativo entre humanos y agentes de inteligencia artificial.  
El rediseño visual abandona los acrónimos genéricos para consolidar una **identidad de ingeniería pura**:
- **Los Corchetes de Terminal `< >`:** Representan el canal dialéctico A2A (Agent-to-Agent) y la consola de comandos de desarrollo.
- **El Puente Central Sólido:** Simboliza la verdad algorítmica inquebrantable, la firma criptográfica Ed25519 y el consenso del Tribunal Borda.
- **El Superíndice `²`:** Eleva a MCP a la segunda potencia: la colaboración cuadrática entre enjambres multi-agente y la soberanía del programador.
- **La Paleta Monocromática Verde Esmeralda:** Evoca terminales UNIX deterministas, éxito en compilación de tests y entornos respetuosos de la privacidad y el cómputo local. Cero distracciones cromáticas.

---

## 2. Paleta Cromática Oficial (Tokens Sólidos)

| Token | Código HEX | RGB | Uso Recomendado |
| :--- | :--- | :--- | :--- |
| `--color-brand-primary` | `#10B981` | `rgb(16, 185, 129)` | Corchetes del isotipo, superíndice `²`, estados activos y éxito. |
| `--color-brand-secondary`| `#34D399` | `rgb(52, 211, 153)` | Subtítulo, iluminación sutil, bordes de enfoque y acentos. |
| `--color-brand-light-primary` | `#059669` | `rgb(5, 150, 105)` | Variante de alto contraste para interfaces en modo claro. |
| `--color-brand-text-light` | `#FFFFFF` | `rgb(255, 255, 255)` | Palabra `MCP` y `P` en modo oscuro, puente central del isotipo. |
| `--color-brand-text-dark` | `#0F172A` | `rgb(15, 23, 42)` | Palabra `MCP²P` en modo claro, texto tipográfico de alto contraste. |
| `--color-brand-bg-dark` | `#0B0F19` | `rgb(11, 15, 25)` | Fondo oficial para launchers, favicons y contenedor de escritorio. |
| `--color-brand-bg-light` | `#F0FDF4` | `rgb(240, 253, 244)` | Fondo para variantes en modo claro / documentos imprimibles. |

---

## 3. Tipografía Oficial

- **Familia Tipográfica del Wordmark:**  
  `system-ui, -apple-system, 'Inter', 'Segoe UI', Roboto, sans-serif`  
  - Peso: `font-weight: 800` (Extra Bold).  
  - Kerning: `letter-spacing: -0.5px`.  
  - Superíndice: `font-size: 64%`, `dy: -15px` relativo a la línea base.
- **Familia Tipográfica del Subtítulo:**  
  `system-ui, -apple-system, 'Inter', sans-serif`  
  - Peso: `font-weight: 700` (Bold).  
  - Kerning: `letter-spacing: 2.2px` (Espaciado amplio para máxima legibilidad).
- **Tipografía para Código / Monospace (UI Companion):**  
  `'JetBrains Mono', 'Fira Code', 'SF Mono', monospace`

---

## 4. Estructura de Assets en `brand/`

```text
brand/
├── core/                                         # SVGs vectoriales nativos para UIs y aplicaciones
│   ├── logo-mcp2p-dark.svg                       # Imagotipo horizontal completo (Modo Oscuro)
│   ├── logo-mcp2p-light.svg                      # Imagotipo horizontal completo (Modo Claro)
│   ├── icon-dialectic-shield.svg                 # Isotipo / Escudo cuadrado con fondo oscuro
│   ├── icon-dialectic-shield-transparent.svg     # Isotipo vectorial puro sin fondo
│   ├── icon-dialectic-shield-light.svg           # Isotipo con fondo claro
│   └── wordmark-mcp2p.svg                        # Logotipo tipográfico MCP²P aislado
│
├── favicons/                                     # Favicons y web icons optimizados
│   ├── favicon.ico                               # Multi-resolución (16, 32, 48 px)
│   ├── favicon-16x16.png                         # Pestaña estándar de navegador
│   ├── favicon-32x32.png                         # Pestaña HD / Retina
│   ├── favicon-48x48.png                         # Barra de tareas del sistema operativo
│   ├── apple-touch-icon-180x180.png             # Icono de pantalla de inicio iOS / macOS
│   ├── android-chrome-192x192.png               # PWA Android / Chrome
│   ├── icon-512x512.png                         # Icono de aplicación alta resolución
│   └── icon-512x512.webp                        # Formato web ultra-optimizado
│
├── png/                                          # Renders PNG de alta fidelidad
│   ├── logo-mcp2p-dark-800w.png                  # Imagotipo completo 800px ancho
│   ├── logo-mcp2p-dark-400w.png                  # Imagotipo completo 400px ancho
│   ├── icon-dialectic-shield-1024x1024.png       # App Store / Instalador / Splash
│   ├── icon-dialectic-shield-512x512.png         # Icono desktop 512px
│   ├── icon-dialectic-shield-256x256.png         # Icono desktop 256px
│   └── icon-dialectic-shield-128x128.png         # Icono desktop 128px
│
├── social/                                       # Recursos para redes y GitHub
│   ├── og-image-1200x630.png                     # Open Graph para links y documentación web
│   ├── github-social-preview-1280x640.png        # Header oficial para el repositorio GitHub
│   └── twitter-header-1500x500.png               # Banner para redes sociales
│
├── preview.html                                  # Galería visual interactiva local
└── BRAND_SYSTEM.md                               # Este documento
```
