FROM python:3.12-slim

ARG BINDERPKG_REF=main
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && git clone --depth 1 --branch "${BINDERPKG_REF}" \
       https://github.com/jaredcasarez/binderpkg.git /opt/binderpkg

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

ENV BINDERPKG_PATH="opt/binderpkg"
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
