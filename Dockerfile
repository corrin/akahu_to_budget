ARG BUILD_FROM=python:3.12-alpine
FROM $BUILD_FROM
ARG BUILD_VERSION=0.1.2

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="aarch64|amd64"

WORKDIR /app/

RUN apk add --no-cache bash jq tzdata

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY modules ./modules
COPY sync_cli.py .
COPY haos_scheduler.py .
COPY sure_client.py .
COPY haos_mapping_bootstrap.py .

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
