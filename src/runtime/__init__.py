"""Utilidades de proceso neutrales (sin dependencias de capa): identidad del nodo local y rutas.

Viven aquí y no en `src/app/` para que adaptadores como `src/storage/db.py` puedan usarlas sin
depender de la capa de aplicación (EGM-55). `src/app/identity.py` y `src/app/paths.py` quedan
como re-exportaciones de compatibilidad.
"""
