ARG BUILD_FROM
FROM ${BUILD_FROM}

# lxml benötigt zur Laufzeit libxml2/libxslt; zum Bauen die Dev-Pakete.
RUN apk add --no-cache libxml2 libxslt

WORKDIR /app

# Abhängigkeiten zuerst (bessere Layer-Zwischenspeicherung)
COPY requirements.txt /app/requirements.txt
RUN apk add --no-cache --virtual .build-deps gcc musl-dev libxml2-dev libxslt-dev \
    && pip install --no-cache-dir -r /app/requirements.txt \
    && apk del .build-deps

# Anwendungscode
COPY kfz_crawler /app/kfz_crawler
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
