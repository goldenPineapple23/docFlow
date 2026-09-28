FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends util-linux iproute2 ca-certificates \
 && (apt-get install -y --no-install-recommends util-linux-extra || true) \
 && rm -rf /var/lib/apt/lists/* \
 && which unshare nsenter setpriv ip \
 && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin parse
COPY probe.py /app/probe.py
RUN chmod 755 /app && chmod 644 /app/probe.py
CMD ["sleep", "infinity"]
