# ==============================================================================
# Multi-stage Dockerfile para MCP2P Desktop / Headless Runner
# ==============================================================================

# Stage 1: Build de la interfaz React con Vite
FROM node:20-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --prefer-offline --no-audit || npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Entorno de ejecución en Python
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema para soporte gráfico (WebKit2GTK moderno 4.1 / 4.0)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    gir1.2-webkit2-4.1 || apt-get install -y --no-install-recommends gir1.2-webkit2-4.0 \
    libwebkit2gtk-4.1-dev || apt-get install -y --no-install-recommends libwebkit2gtk-4.0-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar archivos del proyecto
COPY . /app

# Copiar bundle compilado del frontend asegurando que no dependa del host
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Instalar requerimientos mínimos de Python
RUN pip install --no-cache-dir pywebview mcp pydantic

# Puerto P2P LAN para descubrimiento y sincronización entre nodos
EXPOSE 42424

CMD ["python3", "-m", "src.app.main"]
