# Imagen del sandbox de cómputo de M37 (backend `container`).
#
#   docker build -t m37-sandbox:latest extensions/sandbox
#
# El kernel la ejecuta con: --network none, --cap-drop ALL, --read-only, sin secretos en el entorno y montando
# ÚNICAMENTE los directorios del encargo activo. Aquí solo va el stack de análisis; el motor M37 no se instala dentro.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

WORKDIR /m37
