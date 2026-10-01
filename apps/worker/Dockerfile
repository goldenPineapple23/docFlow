# The worker on Fly staging (Stage 3c, item 12). Since 3c it opens no file a
# stranger sent: the parse service does (apps/parse). Built from the
# repository root:
#
#     docker build -f apps/worker/Dockerfile -t docflow-worker .
#
# Pinned like the parse image: the base by digest, Debian packages from the
# same dated snapshot (DEBIAN_SNAPSHOT, moved by the weekly job; D-183),
# Python packages by apps/worker/requirements.lock.txt.
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ARG DEBIAN_SNAPSHOT=20261001T000000Z
RUN sed -i \
      -e "s#http://deb.debian.org/debian-security#http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}#" \
      -e "s#http://deb.debian.org/debian\$#http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}#" \
      /etc/apt/sources.list.d/debian.sources \
 && ! grep -q deb.debian.org /etc/apt/sources.list.d/debian.sources \
 && apt-get -o Acquire::Check-Valid-Until=false -o Acquire::Retries=5 update \
 && apt-get -o Acquire::Retries=5 -y upgrade \
 && apt-get -o Acquire::Retries=5 install -y --no-install-recommends python3 python3-venv ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10100 docflow \
 && useradd --system --uid 10100 --gid 10100 --no-create-home --shell /usr/sbin/nologin docflow

COPY apps/worker/requirements.lock.txt /opt/worker/requirements.lock.txt
RUN python3 -m venv /opt/worker/venv \
 && /opt/worker/venv/bin/pip install --no-cache-dir --no-deps -r /opt/worker/requirements.lock.txt

COPY packages/core/pyproject.toml /opt/worker/src/core/pyproject.toml
COPY packages/core/docflow_core /opt/worker/src/core/docflow_core
RUN /opt/worker/venv/bin/pip install --no-cache-dir --no-deps /opt/worker/src/core
COPY apps/worker/app /opt/worker/app

WORKDIR /opt/worker
ENV PATH=/opt/worker/venv/bin:/usr/bin:/bin
USER docflow
# The default process. On Fly, apps/worker/fly.toml's [processes] runs this
# image twice: `worker` (this command) and `beat`, exactly one machine
# (Stage 3d, change C).
CMD ["celery", "-A", "app.celery_app", "worker", "--loglevel=info", "-Q", "interactive,bulk"]
