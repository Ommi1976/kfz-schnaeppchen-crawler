ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
FROM ${BUILD_FROM}

ARG BUILD_VERSION
ARG BUILD_ARCH
LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Abhängigkeiten möglichst über apt (keine Kompilierung nötig -> schneller,
# stabiler Build). lxml/bs4/requests/yaml kommen als Debian-Pakete; rich und
# PySocks sind reine Python-Wheels und werden per pip nachgezogen.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-lxml \
        python3-bs4 \
        python3-requests \
        python3-yaml \
        curl \
        gosu \
        netbase \
        ca-certificates \
    && python3 -m pip install --no-cache-dir --break-system-packages rich PySocks \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY kfz_crawler /app/kfz_crawler
COPY run.sh /run.sh

# Absicherung gegen Windows-Zeilenenden: käme run.sh mit CRLF an, scheitert der
# Shebang und der Container startet endlos neu.
RUN tr -d '\r' < /run.sh > /run.sh.tmp && mv /run.sh.tmp /run.sh \
    && chmod a+x /run.sh \
    && python3 -m compileall -q /app/kfz_crawler \
    && useradd --system --uid 10002 --home /data --shell /usr/sbin/nologin crawler \
    && mkdir -p /data \
    && chown -R crawler:crawler /app /data

CMD [ "/run.sh" ]
