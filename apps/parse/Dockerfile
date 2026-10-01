# The parse service (Stage 3c): the only place DocFlow opens a file a stranger
# sent, one sandboxed job per file. CI and Fly build this same file, from the
# repository root:
#
#     docker build -f apps/parse/Dockerfile -t docflow-parse .
#
# Pinned, so what CI tested is what runs: the base image by digest, Debian
# packages by exact version, Python packages by apps/parse/requirements.lock.txt.
# Changing any pin is a deliberate upgrade (RUNBOOK section 8), and the Fly
# B tests must pass on staging before it reaches production (RUNBOOK 8.1).
#
# Debian 13 ("trixie"): its own Python is 3.13 (what CI uses) and it ships the
# libseccomp binding (python3-seccomp) the job's filter is built with.
FROM debian:trixie-slim@sha256:a99cfc517144bc59b1978475ec53b46ecabec7e43635402ee5b77cc54cd1b20a

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3=3.13.5-1 \
      python3-venv=3.13.5-1 \
      python3-seccomp=2.6.0-2 \
      libseccomp2=2.6.0-2 \
      util-linux=2.41.5-0+deb13u1 \
      libreoffice-writer-nogui=4:25.2.3-2+deb13u7 \
 && rm -rf /var/lib/apt/lists/* \
 && command -v unshare && command -v setpriv && command -v soffice

# One unprivileged user per job slot (10001, 10002), with no home and no shell.
RUN groupadd --system --gid 10001 parse0 \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin parse0 \
 && groupadd --system --gid 10002 parse1 \
 && useradd --system --uid 10002 --gid 10002 --no-create-home --shell /usr/sbin/nologin parse1 \
 && mkdir -p /work /lohome /opt/parse

# The parsing libraries, exactly as locked. --system-site-packages only so the
# job can import Debian's python3-seccomp.
COPY apps/parse/requirements.lock.txt /opt/parse/requirements.lock.txt
RUN python3 -m venv --system-site-packages /opt/parse/venv \
 && /opt/parse/venv/bin/pip install --no-cache-dir --no-deps -r /opt/parse/requirements.lock.txt

# docflow_core without its dependencies (the service imports only its
# standard-library parts, file_types), then the service itself.
COPY packages/core/pyproject.toml /opt/parse/src/core/pyproject.toml
COPY packages/core/docflow_core /opt/parse/src/core/docflow_core
COPY apps/parse/pyproject.toml /opt/parse/src/parse/pyproject.toml
COPY apps/parse/parse_service /opt/parse/src/parse/parse_service
RUN /opt/parse/venv/bin/pip install --no-cache-dir --no-deps /opt/parse/src/core /opt/parse/src/parse \
 && /opt/parse/venv/bin/python -c "import parse_service.server, seccomp, docflow_core.file_types"

ENV PATH=/opt/parse/venv/bin:/usr/local/bin:/usr/bin:/bin
EXPOSE 8100
# Runs as root on purpose: creating namespaces and cgroups needs it. Every
# job drops to its slot's user before it reads a byte (BUILD-STATUS 3c, item 3).
CMD ["/opt/parse/venv/bin/python", "-m", "parse_service.server"]
