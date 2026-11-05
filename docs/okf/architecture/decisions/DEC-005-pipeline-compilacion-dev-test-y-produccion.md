---
id: DEC-005
title: Pipeline de Compilación Dual (develop=test / main=producción) con Auto-Updater
status: in_discussion
related_ids: [RNF-04, DEC-003]
created_at: 2026-11-04T00:00:00Z
---
# DEC-005: Pipeline de Compilación Dual (develop=test / main=producción) con Auto-Updater

> **Estado**: propuesta para discusión, **no implementada**. Explícitamente deprioritizada por el usuario respecto a cerrar primero los bugs de los planes de reparación v1-v4. No se tocó código al escribir esto.
> **Precedente ya validado**: este mismo patrón ya está implementado y funcionando en el proyecto Parma del usuario (`ADR-002` y `ADR-004` de `docs/ADR.md` en ese repo, más `.github/workflows/build-and-release.yml` real). No es una idea especulativa — es replicar algo que ya probaron.

## Contexto

El usuario quiere poder probar cada push a `main` con un binario compilado real (`.exe` con auto-actualizador), igual que ya tienen en Parma, para no depender de actualizar manualmente ni de confiar solo en los 72 tests unitarios. La duda planteada fue si esto necesita dos ejecutables con rutas de instalación distintas, o alguna otra separación — quedó resuelta al revisar cómo Parma lo resolvió en la práctica.

## Cómo lo resolvió Parma (el precedente)

Un solo workflow de GitHub Actions (`build-and-release.yml`), disparado en push a **ambas** ramas (`develop` y `main`):
- **En cualquiera de las dos ramas**: compila los binarios (Windows instalador + portable, Linux .deb + tar.gz) y los sube como *artifacts* de GitHub Actions con `retention-days: 14` — quedan disponibles para descargar y probar, pero no son públicos ni oficiales.
- **Solo en `main`, y solo en push (no en PR)**: un job separado `publish-release` (`if: github.ref == 'refs/heads/main' && github.event_name == 'push'`) toma esos mismos binarios, genera el tag de versión y publica la Release pública oficial en GitHub.

Es decir: **no son dos ejecutables con rutas distintas, es el mismo pipeline con un paso final condicionado a la rama.** `develop` nunca "contamina" la release pública porque el paso de publicación simplemente no corre ahí.

## Decisión propuesta para MCP2P

1. **Reusar la misma estructura de un solo workflow con publicación condicionada**, en vez de mantener dos pipelines separados o dos nombres de ejecutable con rutas distintas. Responde directamente a la duda que planteaste: no hace falta "otro .exe con otras rutas" — el build de `develop` es un artefacto temporal de CI (14 días), no una instalación paralela permanente.
2. **Nombrar el artefacto de prueba distinto al de producción** (p. ej. `mcp2p-test.exe` / `mcp2p-test-portable.zip` para lo que sale de `develop`, vs `mcp2p.exe` oficial para lo que sale de `main`) — esto sí es solo cuestión de nombre de archivo en el paso de build, no de arquitectura distinta.
3. **Auto-updater**: adaptar el patrón de `ADR-004` de Parma (descarga asíncrona en trozos + reinicio silencioso `/SILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS` en Windows) para que el MCP2P de producción se actualice solo contra los releases oficiales de `main` — igual que Parma, para no tener que actualizar a mano.
4. **Gate de promoción**: igual que `ADR-002` de Parma dice *"main se activa únicamente tras pruebas satisfactorias en develop"* — formalizar que nada se taggea/publica desde `main` sin que la build de `develop` haya corrido verde primero (esto conecta con `DEC-003` de MCP2P, el aislamiento de worktrees, y con la disciplina de tests que ya se viene exigiendo en los planes v1-v4).

## Explícitamente fuera de alcance por ahora

Por decisión directa del usuario: **no se implementa todavía**. Primero hay que cerrar los bugs activos (planes de reparación v1-v4 de MCP2P). Esto queda registrado para retomarlo después, no como tarea actual de Antigravity.

## Qué falta decidir antes de `approved`

- Si MCP2P necesita Windows+Linux+macOS en el mismo workflow (Parma solo hace Windows+Linux hoy).
- Si el auto-updater de MCP2P debe ser código propio o literalmente reusar/adaptar `desktop_api/auto_updater.py` de Parma como referencia de implementación.
- Nombre final del artefacto de test (`mcp2p-test.exe` es solo una propuesta de este documento, no está decidido).
