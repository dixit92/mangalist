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
#
# Chapter downloads go through one of two programs, chosen in Settings > Connected services: Suwayomi-Server (its own
# container, reached over the network) or MangaList's built-in extension host (extension-host/, a JVM program running
# Mihon / Tachiyomi extensions without Suwayomi). The host is built in a JDK stage and runs on a jlink'd Java 21 runtime
# in the final image, supervised as the "extension-host" service (rootfs/etc/services.d/extension-host): while
# "Built-in extensions" is chosen, and on demand while Suwayomi is (the Extensions page, a test, a carry-over, a chapter
# download of the host still to be finished). Its data: /config/extension-host; its log: /config/logs/extension-host.log.
ARG BASEIMAGE=jlesage/baseimage-gui:debian-12-v4.14.0
ARG JDKIMAGE=eclipse-temurin:21-jdk

# --- the extension host: built from extension-host/ with its Gradle wrapper (Gradle and the dependencies download here)
FROM ${JDKIMAGE} AS exthost
COPY extension-host /src/extension-host
WORKDIR /src/extension-host
RUN ./gradlew --no-daemon -q :host:installDist \
    && cp -r host/build/install/mangalist-extension-host /opt/extension-host

# --- a trimmed Java 21 runtime for it (jlink): the Java SE modules plus what the host and its extensions use
# (com.sun.net.httpserver, sun.misc.Unsafe, EC crypto, zip file systems, DNS, all charsets / locales).
FROM ${JDKIMAGE} AS jre
RUN jlink --add-modules java.se,jdk.httpserver,jdk.unsupported,jdk.crypto.ec,jdk.crypto.cryptoki,jdk.zipfs,jdk.charsets,jdk.localedata,jdk.naming.dns,jdk.management \
        --strip-debug --no-man-pages --no-header-files --compress=zip-6 --output /opt/jre

FROM ${BASEIMAGE}

# Python, the Qt xcb platform plugin's libraries, and a font (the base image ships none for Qt).
RUN add-pkg \
        python3 python3-venv \
        libegl1 libgl1 libglib2.0-0 libdbus-1-3 libfontconfig1 libxkbcommon0 libxkbcommon-x11-0 \
        libx11-xcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
        libxcb-render-util0 libxcb-shape0 libxcb-xfixes0 libxcb-xinerama0 libxcb-xkb1 \
        fonts-dejavu-core

# A file manager for the List's "Open folder" (and the other Open folder buttons): the container has no desktop
# for Qt to hand a folder to, so MANGALIST_FILE_MANAGER (below) names this one; it opens as a window in the web GUI.
# Deleting skips the trash, after a confirmation: a trash for /data would be a ".Trash-99" folder at the top of the
# mount - on Unraid, a new share. Its own settings (Edit > Preferences, kept in /config) can change that.
RUN add-pkg pcmanfm \
    && sed -i 's/^use_trash=1$/use_trash=0/' /etc/xdg/libfm/libfm.conf \
    && grep -qx 'use_trash=0' /etc/xdg/libfm/libfm.conf

# The extension host's WebView (KCEF: a Chromium bundle the host downloads into /config/extension-host on first use)
# needs Chromium's shared libraries; the X / font libraries above serve it too.
RUN add-pkg libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libgbm1 libxcomposite1 libxdamage1 \
        libxrandr2 libxtst6 libasound2 libpango-1.0-0 libcairo2 libxshmfence1

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

# The web GUI's icons (browser tab, home-screen) from MangaList's icon (mangalist/gui/app_icon.py, rendered into
# packaging/icons/ by packaging/make_icon.py --repo). Before the app code, so this layer stays cached.
COPY packaging/icons/mangalist-512.png /tmp/mangalist-icon.png
RUN install_app_icon.sh "file:///tmp/mangalist-icon.png" && rm -f /tmp/mangalist-icon.png

# Declared here, not at the top: a changing version stamp must not invalidate the layers above.
ARG DOCKER_IMAGE_VERSION=dev

COPY --from=jre /opt/jre /opt/mangalist/jre
COPY --from=exthost /opt/extension-host /opt/mangalist/extension-host

COPY mangalist /opt/mangalist/app/mangalist
COPY packaging/docker/startapp.sh /startapp.sh
COPY packaging/docker/rootfs/ /

RUN chmod 755 /startapp.sh /etc/services.d/headless/run /etc/services.d/headless/disabled \
        /etc/services.d/extension-host/run /etc/services.d/extension-host/disabled \
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
    # Started by the GUI's Open folder buttons (mangalist/gui/links.py).
    MANGALIST_FILE_MANAGER=pcmanfm \
    # Headless runner service: 0 turns it off. Downloads stay off unless MANGALIST_DOWNLOADS=1.
    MANGALIST_HEADLESS=1 \
    MANGALIST_RESCAN_SCHEDULE=daily@03:30 \
    MANGALIST_DOWNLOADS=0 \
    MANGALIST_DISPATCH_SCHEDULE=daily@04:30 \
    # Volumes MVP (with MANGALIST_DOWNLOADS=1): file finished downloads, remove completed torrents.
    MANGALIST_DOWNLOADS_SCHEDULE=every:1h \
    MANGALIST_CATCH_UP=1 \
    # A UTF-8 locale for every process (the base image's en_US.UTF-8 is not installed, so the C library fell back to
    # ASCII and the extension host could not write a chapter file named after "Les Poroïniens": 2026-10-24). C.UTF-8
    # is built into glibc; the supervisor also starts the host in it.
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    # Built-in extensions: the extension host runs while Settings chooses it or asks for it (auto), always (1) or
    # never (0). FlareSolverr and the repository are set in Settings, else here; the JVM's heap is capped.
    MANGALIST_EXTENSION_HOST=auto \
    # Where the host downloads chapters: on the /data share (Unraid's appdata, next to Suwayomi's old folder), so a
    # finished chapter is hard-linked into the library instead of copied across filesystems. Used when the folder or
    # its parent exists (/data/appdata/mangalist is this container's /config on a usual Unraid set-up); else
    # /config/extension-host/downloads.
    MANGALIST_EXTENSION_DOWNLOADS=/data/appdata/mangalist/extension-host-downloads \
    MANGALIST_EXTENSION_HOST_BIN=/opt/mangalist/extension-host/bin/mangalist-extension-host \
    MANGALIST_EXTENSION_HOST_OPTS="-Xms32m -Xmx768m -XX:+UseSerialGC" \
    JAVA_HOME=/opt/mangalist/jre \
    # A WARNING in the log when the window is blocked for more than 250 ms (mangalist/gui/watchdog.py).
    MANGALIST_UI_WATCHDOG=1 \
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
    net.unraid.docker.icon="https://raw.githubusercontent.com/dixit92/MangaList/main/packaging/icons/mangalist-512.png"
