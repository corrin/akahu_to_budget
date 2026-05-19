ARG BUILD_FROM=python:3.12-alpine
FROM $BUILD_FROM

WORKDIR /app/

RUN apk add --no-cache bash jq

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY modules ./modules
COPY sync_cli.py .

COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
