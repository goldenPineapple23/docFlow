# The API on Fly staging (Stage 3c, item 12). It never opens a file a stranger
# sent (CLAUDE.md 7.11; apps/api/tests/test_parsing_boundary.py) and never
# holds the parse token: on Fly it refuses to start with one (Q12). Built from
# the repository root:
#
#     docker build -f apps/api/Dockerfile -t docflow-api .
#
# Pinned like the parse image: the base by digest, Debian packages from the
# same dated snapshot (DEBIAN_SNAPSHOT, moved by the weekly job; D-183),
# Python packages by apps/api/requirements.lock.txt.
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

COPY apps/api/requirements.lock.txt /opt/api/requirements.lock.txt
RUN python3 -m venv /opt/api/venv \
 && /opt/api/venv/bin/pip install --no-cache-dir --no-deps -r /opt/api/requirements.lock.txt

COPY packages/core/pyproject.toml /opt/api/src/core/pyproject.toml
COPY packages/core/docflow_core /opt/api/src/core/docflow_core
RUN /opt/api/venv/bin/pip install --no-cache-dir --no-deps /opt/api/src/core
COPY apps/api/app /opt/api/app

WORKDIR /opt/api
ENV PATH=/opt/api/venv/bin:/usr/bin:/bin
USER docflow
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "::", "--port", "8000"]
