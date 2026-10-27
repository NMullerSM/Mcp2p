# Dockerfile para ejecución aislada o soporte macOS
FROM python:3.11-slim

# Instalar dependencias del sistema para soporte gráfico o servidor web
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libwebkit2gtk-4.0-dev \
    gir1.2-webkit2-4.0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar archivos del proyecto
COPY . /app

# Instalar requerimientos mínimos de Python
RUN pip install --no-cache-dir pywebview mcp

EXPOSE 8080

CMD ["python3", "-m", "src.app.main"]
