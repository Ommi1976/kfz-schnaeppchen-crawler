# Messcontainer: Chrome Stable auf der Intel-GPU des HA-Servers, ohne Xvfb.
# Nur für die Messung, nicht Teil des Add-ons.
FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    TZ=Europe/Berlin \
    LANG=de_DE.UTF-8 \
    XDG_RUNTIME_DIR=/tmp/xdg \
    WLR_BACKENDS=headless \
    WLR_RENDERER=gles2 \
    WLR_RENDER_DRM_DEVICE=/dev/dri/renderD128 \
    WLR_LIBINPUT_NO_DEVICES=1

# cage: Wayland-Compositor ohne Bildschirm, rendert über die GPU (statt Xvfb).
# patchright: Playwright-Ersatz ohne --enable-automation und ohne Runtime.enable.
# Unversioniert, weil es ein einmaliges Experiment ist.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-pip ca-certificates curl tzdata locales cage xwayland \
        libgl1-mesa-dri libegl1 libegl-mesa0 libgbm1 mesa-vulkan-drivers \
        fonts-liberation fonts-dejavu-core \
    && sed -i 's/^# *de_DE.UTF-8/de_DE.UTF-8/' /etc/locale.gen && locale-gen \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir --break-system-packages \
        patchright curl_cffi==0.16.2

# Chrome direkt von Google statt "patchright install chrome": dessen
# FFmpeg-Download (nur für Videoaufnahmen) läuft hier in einen Timeout.
RUN curl -fsSL -o /tmp/chrome.deb \
        https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends /tmp/chrome.deb \
    && rm /tmp/chrome.deb && apt-get clean && rm -rf /var/lib/apt/lists/*

COPY start.sh messung.py cookie_test.py /app/
RUN tr -d '\r' < /app/start.sh > /app/start.tmp && mv /app/start.tmp /app/start.sh \
    && chmod a+x /app/start.sh \
    && useradd --create-home --uid 1000 messung \
    && mkdir -p /out /eingabe && chown messung:messung /out /eingabe

# cage verweigert den Start als root.
USER messung
ENTRYPOINT ["/app/start.sh"]
CMD ["gpu"]
