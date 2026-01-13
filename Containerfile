FROM python:3.12-alpine

WORKDIR /app/
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY modules ./modules
COPY ["README.md", "LICENSE", "*.py", "."]

ENTRYPOINT ["python", "flask_app.py"]
CMD ["--sync"] # Default to one-off sync