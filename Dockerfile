FROM python:3.11-slim

# Instalar ffmpeg y ffprobe (necesarios para MP3 y MP4)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el código
COPY . .

# Crear carpeta temporal
RUN mkdir -p temp_downloads

# Ejecutar el bot
CMD ["python", "main.py"]
