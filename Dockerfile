ARG BUILD_FROM=python:3.12-alpine
FROM $BUILD_FROM
ARG BUILD_VERSION=0.1.0

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="aarch64|amd64"

WORKDIR /app/

RUN apk add --no-cache bash jq

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY modules ./modules
COPY sync_cli.py .
COPY sure_client.py .

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
