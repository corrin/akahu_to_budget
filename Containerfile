FROM python:3.12-alpine

WORKDIR /app/
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY modules ./modules
COPY ["README.md", "LICENSE", "*.py", "."]

RUN adduser -D app && chown -R app:app /app
USER app

EXPOSE 5000

ENTRYPOINT ["python", "flask_app.py"]
# Default to a one-off sync. Override with an empty arg to launch the webhook
# server instead, e.g. `podman run --rm -p 5000:5000 <image> ''`.
CMD ["--sync"]
