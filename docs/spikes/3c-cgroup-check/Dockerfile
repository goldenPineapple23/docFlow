# Throwaway image for the 3c cgroup v2 / seccomp check on a real Fly machine.
# Holds no customer data and no DocFlow credential. Destroyed after the run.
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-seccomp util-linux procps \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin parse
COPY check.py check2.py /app/
RUN chmod 755 /app && chmod 644 /app/check.py /app/check2.py
CMD ["sleep", "infinity"]
