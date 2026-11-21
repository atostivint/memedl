# memedl — image (gallery-dl + service d'intake + pont ntfy)
#
# Le contexte de build est ce dossier. On épingle gallery-dl : une montée de
# version amont casse Instagram sans prévenir, donc on choisit la version.
FROM python:3.12-slim

ARG GALLERY_DL_VERSION=1.32.19
ARG YTDLP_VERSION=2026.10.26

# ffmpeg : vidéo+audio séparés, conversion des MP4 muets de X en vrai .gif.
# curl : sonde de santé du conteneur.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir "gallery-dl==${GALLERY_DL_VERSION}" "yt-dlp==${YTDLP_VERSION}" \
 && useradd --uid 1000 --create-home --shell /usr/sbin/nologin memedl

WORKDIR /app
COPY memedl.py memedl-ntfy.py /app/

# /data est le montage SMB (partage NAS) ; /state garde l'historique local
# (URL déjà vues, id ntfy du dernier message).
RUN mkdir -p /data /state && chown -R memedl:memedl /data /state /app
USER 1000:1000

ENV MEMEDL_DIR=/data/library \
    MEMEDL_BIND=0.0.0.0 \
    MEMEDL_PORT=8765 \
    NT_MEMEDL_SINCE_FILE=/state/since \
    PYTHONUNBUFFERED=1

EXPOSE 8765

# le conteneur exécute memedl ; le pont ntfy tourne dans un second conteneur
# du même pod, sur la même image, avec `command: [python3, /app/memedl-ntfy.py]`
CMD ["python3", "/app/memedl.py"]
