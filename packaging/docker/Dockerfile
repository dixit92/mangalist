# MangaList for Unraid / Docker: the desktop GUI in the browser (jlesage/baseimage-gui: TigerVNC +
# noVNC + nginx) plus the headless runner (scheduled rescans; downloads opt-in) as a second service.
#
#   docker build -f packaging/docker/Dockerfile -t mangalist .
#   docker run -d -p 5800:5800 -v <appdata>:/config -v <library share>:/data mangalist
#   -> https://<host>:5800/   (self-signed certificate unless you put your own in /config/certs)
#
# Local use only: a personal, single-user app, not a server. The web GUI has no login (by design) and
# can rename / move files under /data - never expose the port to the internet; use a VPN or Tailscale.
#
# Volumes: /config = MangaList's data folder (settings, MangaUpdates cache, logs, scheduler state)
#          plus the base image's own files (certs/, xdg/, log/); /data = the media, read-write.
#
# Base image: Debian 12 (glibc, so the PySide6 wheels from PyPI work; Alpine's musl does not) with
# Debian's Python 3.11, which the test matrix covers. Pinned to an exact base version; bump it here.
ARG BASEIMAGE=jlesage/baseimage-gui:debian-12-v4.14.0
FROM ${BASEIMAGE}

# Python, the Qt xcb platform plugin's libraries, and a font (the base image ships none for Qt).
RUN add-pkg \
        python3 python3-venv \
        libegl1 libgl1 libglib2.0-0 libdbus-1-3 libfontconfig1 libxkbcommon0 libxkbcommon-x11-0 \
        libx11-xcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
        libxcb-render-util0 libxcb-shape0 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 \
        fonts-dejavu-core

# Dependencies first (cached while only the code changes). The GUI uses QtCore / QtGui / QtWidgets
# only, so the image installs PySide6-Essentials instead of the full PySide6 (saves ~250 MB).
COPY requirements.txt /opt/mangalist/requirements.txt
RUN python3 -m venv /opt/mangalist/venv \
    && sed 's/^PySide6\([<>=~! ]\)/PySide6-Essentials\1/' /opt/mangalist/requirements.txt \
        > /tmp/requirements-image.txt \
    && /opt/mangalist/venv/bin/pip install --no-cache-dir --disable-pip-version-check \
        -r /tmp/requirements-image.txt \
    && rm /tmp/requirements-image.txt \
    && find /opt/mangalist/venv -name '__pycache__' -prune -exec rm -rf {} +

# Declared here, not at the top: a changing version stamp must not invalidate the layers above.
ARG DOCKER_IMAGE_VERSION=dev

COPY mangalist /opt/mangalist/app/mangalist
COPY packaging/docker/startapp.sh /startapp.sh
COPY packaging/docker/rootfs/ /

RUN chmod 755 /startapp.sh /etc/services.d/headless/run /etc/services.d/headless/disabled \
    && /opt/mangalist/venv/bin/python -m compileall -q /opt/mangalist/app \
    && APP_VERSION="$(cd /opt/mangalist/app && /opt/mangalist/venv/bin/python -m mangalist --version | sed 's/^MangaList //')" \
    && set-cont-env APP_NAME "MangaList" \
    && set-cont-env APP_VERSION "$APP_VERSION" \
    && set-cont-env DOCKER_IMAGE_VERSION "$DOCKER_IMAGE_VERSION" \
    # The headless runner may be mid-rescan when the container stops; give it 10 s (default 5 s).
    && set-cont-env SERVICES_GRACETIME 10000

ENV \
    # Unraid defaults: nobody:users owns the shares and appdata.
    USER_ID=99 \
    GROUP_ID=100 \
    # HTTPS for the web GUI. Browsers grant clipboard access only in a secure context, so HTTPS is
    # what makes two-way copy / paste with the host (WEB_HOST_CLIPBOARD_SYNC) work; over plain HTTP
    # only the side-panel clipboard does. A self-signed certificate is generated in /config/certs.
    SECURE_CONNECTION=1 \
    WEB_HOST_CLIPBOARD_SYNC=1 \
    KEEP_APP_RUNNING=1 \
    # MangaList's data folder (mangalist/paths.py) = the /config volume.
    MANGALIST_DATA_DIR=/config \
    # Headless runner service: 0 turns it off. Downloads stay off unless MANGALIST_DOWNLOADS=1.
    MANGALIST_HEADLESS=1 \
    MANGALIST_RESCAN_SCHEDULE=daily@03:30 \
    MANGALIST_DOWNLOADS=0 \
    MANGALIST_DISPATCH_SCHEDULE=daily@04:30 \
    MANGALIST_CATCH_UP=1 \
    PYTHONPATH=/opt/mangalist/app \
    PYTHONDONTWRITEBYTECODE=1

VOLUME ["/config", "/data"]
EXPOSE 5800

LABEL \
    org.opencontainers.image.title="MangaList" \
    org.opencontainers.image.description="Scan a manga library, link it with MangaUpdates and see how far behind each series is - in the browser, with scheduled rescans." \
    org.opencontainers.image.source="https://github.com/dixit92/MangaList" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.version="${DOCKER_IMAGE_VERSION}" \
    net.unraid.docker.webui="https://[IP]:[PORT:5800]/" \
    net.unraid.docker.icon="https://raw.githubusercontent.com/dixit92/unraid-templates/main/icon.png"
