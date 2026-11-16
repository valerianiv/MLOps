FROM python:3.12.9-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /lab
COPY requirements.lock .
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY app app
COPY tools tools
COPY artifacts artifacts
USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "app.server"]
