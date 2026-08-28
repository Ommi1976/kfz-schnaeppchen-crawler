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

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-lxml \
        python3-bs4 \
        python3-requests \
        python3-yaml \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
        libtesseract-dev \
        tor \
        curl \
        gosu \
        netbase \
        ca-certificates \
    && python3 -m pip install --no-cache-dir --break-system-packages rich PySocks fastapi uvicorn curl_cffi playwright pytesseract pillow \
    && mkdir -p /opt/playwright-browsers \
    && playwright install --with-deps chromium firefox \
    && chmod -R a+rx /opt/playwright-browsers \
    && echo -e "SocksPort 127.0.0.1:9050\nControlPort 127.0.0.1:9051\nCookieAuthentication 0\nDataDirectory /data/tor" > /etc/tor/torrc \
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

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8099/api/ready || exit 1

CMD [ "/run.sh" ]
