"""
D-150 proof spike: can a parse process on a Fly Machine be cut off from every network?

Run as root on the machine:   python3 /app/probe.py run <target-app-name>
It runs every probe twice:
  OUTSIDE -- as the machine itself (root, machine's own network). This is the
             positive control: the target must be reachable here, or a
             "blocked" result inside would prove nothing.
  INSIDE  -- in the sandbox a parse process would get: a fresh network
             namespace (unshare --net), then dropped to the unprivileged
             `parse` user with no capabilities and no_new_privs.
A network probe PASSES only if OUTSIDE reached the target and INSIDE did not.
The script prints its own evidence and a single VERDICT line.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import socket
import stat
import struct
import subprocess
import sys
import urllib.request

TIMEOUT = 4.0
SOCKET_PATH = "/.fly/api"
INPUT_FILE = "/tmp/spike-input.txt"


# ---------------------------------------------------------------- probes
def tcp(host: str, port: int, family: int) -> dict:
    s = socket.socket(family, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect((host, port))
        return {"reached": True, "detail": "TCP connect succeeded"}
    except OSError as exc:
        return {"reached": False, "detail": f"{type(exc).__name__}: {exc}"}
    finally:
        s.close()


def http_get(url: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT + 4) as r:
            return {"reached": True, "detail": f"HTTP {r.status}"}
    except Exception as exc:  # noqa: BLE001 -- any failure is "not reached"
        return {"reached": False, "detail": f"{type(exc).__name__}: {exc}"}


def http_get_ip6(ip: str, port: int) -> dict:
    return http_get(f"http://[{ip}]:{port}/")


def redis_ping(ip: str, port: int) -> dict:
    s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect((ip, port))
        s.sendall(b"PING\r\n")
        reply = s.recv(64)
        return {"reached": reply.startswith(b"+PONG"), "detail": f"reply {reply!r}"}
    except OSError as exc:
        return {"reached": False, "detail": f"{type(exc).__name__}: {exc}"}
    finally:
        s.close()


def resolve(name: str) -> dict:
    try:
        infos = socket.getaddrinfo(name, 443, proto=socket.IPPROTO_TCP)
        addrs = sorted({i[4][0] for i in infos})
        return {"reached": True, "detail": f"resolved to {', '.join(addrs)}"}
    except OSError as exc:
        return {"reached": False, "detail": f"{type(exc).__name__}: {exc}"}


def dns_query_direct(server: str, name: str) -> dict:
    """Raw DNS AAAA query over UDP straight to a resolver IP (no system resolver)."""
    qid = random.randint(0, 0xFFFF)
    header = struct.pack(">HHHHHH", qid, 0x0100, 1, 0, 0, 0)
    qname = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".")) + b"\x00"
    packet = header + qname + struct.pack(">HH", 28, 1)  # AAAA, IN
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    s = socket.socket(family, socket.SOCK_DGRAM)
    s.settimeout(TIMEOUT)
    try:
        s.sendto(packet, (server, 53))
        data, _ = s.recvfrom(2048)
        answers = struct.unpack(">H", data[6:8])[0]
        return {"reached": True, "detail": f"resolver answered, {answers} answer record(s)"}
    except OSError as exc:
        return {"reached": False, "detail": f"{type(exc).__name__}: {exc}"}
    finally:
        s.close()


def unix_socket(path: str) -> dict:
    if not os.path.exists(path):
        return {"reached": False, "detail": f"{path} does not exist", "absent": True}
    st = os.stat(path)
    perms = f"mode {stat.filemode(st.st_mode)} uid {st.st_uid} gid {st.st_gid}"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    try:
        s.connect(path)
        return {"reached": True, "detail": f"connect succeeded ({perms})"}
    except OSError as exc:
        return {"reached": False, "detail": f"{type(exc).__name__}: {exc} ({perms})"}
    finally:
        s.close()


def run_cmd(cmd: list[str]) -> dict:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        out = (p.stderr or p.stdout).strip().splitlines()
        tail = out[-1] if out else ""
        return {"reached": p.returncode == 0, "detail": f"exit {p.returncode} {tail}".strip()}
    except Exception as exc:  # noqa: BLE001
        return {"reached": False, "detail": f"{type(exc).__name__}: {exc}"}


def interfaces() -> dict:
    """Interfaces of the *current* network namespace, from the kernel's
    per-namespace views: /proc/self/net/dev and netlink (`ip -o link`).

    Run 1 read /sys/class/net instead, which reflects the namespace that
    mounted /sys -- the machine's -- not the calling process's, so it showed
    the machine's eth0 inside the sandbox. That listing is kept separately as
    `sysfs` because it is still worth reporting: it is what the sandbox can
    *see*, not what it can use.
    """
    names = sorted(l.split(":")[0].strip() for l in open("/proc/self/net/dev").readlines()[2:])
    p = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True, timeout=10)
    state = {}
    for line in p.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) >= 3:
            n = parts[1].strip().split("@")[0]
            flags = parts[2].split(">")[0]
            state[n] = "UP" if ",UP" in flags or "<UP" in flags else "down"
    detail = ", ".join(f"{n}({state.get(n, '?')})" for n in names)
    up_non_lo = [n for n in names if n != "lo" and state.get(n) == "UP"]
    sysfs = ", ".join(sorted(os.listdir("/sys/class/net")))
    return {"names": names, "detail": detail, "up_non_lo": up_non_lo, "sysfs": sysfs}


def only_loopback_and_down(ifaces: dict) -> bool:
    """The P13 check: the namespace has a loopback interface and nothing else,
    and the loopback is down. One function, used both inside the sandbox and
    (as its positive control) on the machine itself, where it must fail."""
    return ifaces["names"] == ["lo"] and not ifaces["up_non_lo"] and "lo(down)" in ifaces["detail"]


def read_input(path: str) -> dict:
    try:
        data = open(path, "rb").read()
        return {"reached": True, "detail": f"read {len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()[:16]}"}
    except OSError as exc:
        return {"reached": False, "detail": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------ probe table
def network_probes(ctx: dict) -> dict:
    r: dict = {}
    r["P1 internet IPv4 (1.1.1.1:443, TCP)"] = tcp("1.1.1.1", 443, socket.AF_INET)
    r["P2 internet IPv6 ([2606:4700:4700::1111]:443, TCP)"] = tcp("2606:4700:4700::1111", 443, socket.AF_INET6)
    r["P3 internet HTTPS by name (https://example.com)"] = http_get("https://example.com/")
    r["P4 DNS via system resolver (example.com)"] = resolve("example.com")
    r[f"P5 DNS direct to Fly resolver [{ctx['resolver']}]:53"] = dns_query_direct(ctx["resolver"], f"{ctx['target']}.internal")
    if ctx.get("target_ip"):
        r[f"P6 private net: API stand-in [{ctx['target_ip']}]:8080 (HTTP)"] = http_get_ip6(ctx["target_ip"], 8080)
        r[f"P7 private net: Redis stand-in [{ctx['target_ip']}]:6379 (PING)"] = redis_ping(ctx["target_ip"], 6379)
    if ctx.get("api_ip"):
        r[f"P8 Fly Machines API _api.internal [{ctx['api_ip']}]:4280 (TCP)"] = tcp(ctx["api_ip"], 4280, socket.AF_INET6)
    r["P9 cloud metadata 169.254.169.254:80 (TCP)"] = tcp("169.254.169.254", 80, socket.AF_INET)
    r[f"P10 Fly local API socket {SOCKET_PATH} (unix)"] = unix_socket(SOCKET_PATH)
    return r


def inside_main(ctx: dict) -> None:
    res = network_probes(ctx)
    res["P11 escape: nsenter into machine netns"] = run_cmd(["nsenter", "--net=/proc/1/ns/net", "true"])
    res["P12 escape: bring up an interface (ip link set lo up)"] = run_cmd(["ip", "link", "set", "lo", "up"])
    res["_ifaces"] = interfaces()
    res["P14 sandboxed process can read its input file"] = read_input(INPUT_FILE)
    res["_whoami"] = {"detail": f"uid={os.getuid()} gid={os.getgid()} euid={os.geteuid()}"}
    try:
        capeff = [l for l in open("/proc/self/status") if l.startswith(("CapEff", "NoNewPrivs"))]
        res["_caps"] = {"detail": "; ".join(l.strip() for l in capeff)}
    except OSError:
        pass
    print(json.dumps(res))


# ---------------------------------------------------------------- driver
def first_aaaa(name: str) -> str | None:
    try:
        return socket.getaddrinfo(name, 80, socket.AF_INET6)[0][4][0]
    except OSError:
        return None


def resolver_ip() -> str:
    for line in open("/etc/resolv.conf"):
        if line.startswith("nameserver"):
            return line.split()[1]
    return "fdaa::3"


def main_run(target: str) -> int:
    ctx = {
        "target": target,
        "target_ip": first_aaaa(f"{target}.internal"),
        "api_ip": first_aaaa("_api.internal"),
        "resolver": resolver_ip(),
    }
    with open(INPUT_FILE, "w") as f:
        f.write("stand-in purchase order text for the spike -- no customer data\n")
    os.chmod(INPUT_FILE, 0o644)

    print("=" * 100)
    print("D-150 PROOF SPIKE -- evidence")
    print(f"machine: region={os.environ.get('FLY_REGION')} machine={os.environ.get('FLY_MACHINE_ID')} "
          f"kernel={os.uname().release} user={os.getuid()}")
    print(f"resolver (from /etc/resolv.conf): {ctx['resolver']}")
    print(f"{target}.internal -> {ctx['target_ip']}   _api.internal -> {ctx['api_ip']}")
    print(f"{run_cmd(['unshare', '--version'])['detail']}")

    outside = network_probes(ctx)
    outside["P11 escape: nsenter into machine netns"] = run_cmd(["nsenter", "--net=/proc/1/ns/net", "true"])
    outside["P12 escape: bring up an interface (ip link set lo up)"] = run_cmd(["unshare", "--net", "ip", "link", "set", "lo", "up"])
    outside["P14 sandboxed process can read its input file"] = read_input(INPUT_FILE)
    out_if = interfaces()

    sandbox = ["unshare", "--net", "--",
               "setpriv", "--reuid=parse", "--regid=parse", "--init-groups",
               "--no-new-privs", "--inh-caps=-all", "--bounding-set=-all",
               "python3", "/app/probe.py", "inside", json.dumps(ctx)]
    print(f"sandbox: {' '.join(sandbox[:-1])} '<ctx>'")
    p = subprocess.run(sandbox, capture_output=True, text=True, timeout=180)
    if p.returncode != 0 or not p.stdout.strip():
        print(f"SANDBOX FAILED TO START: exit {p.returncode}\n{p.stderr}")
        print("VERDICT: FAIL -- the sandbox could not be created on this machine")
        return 1
    inside = json.loads(p.stdout.strip().splitlines()[-1])
    print(f"sandbox identity: {inside['_whoami']['detail']}; {inside.get('_caps', {}).get('detail', '')}")
    print(f"interfaces OUTSIDE: {out_if['detail']}")
    print(f"interfaces INSIDE : {inside['_ifaces']['detail']}")
    print(f"/sys/class/net as seen INSIDE (machine's view, visible but not usable): {inside['_ifaces']['sysfs']}")
    print("=" * 100)

    fails, nocontrol, passes = [], [], []
    for name, o in outside.items():
        i = inside[name]
        if name.startswith("P14"):
            ok = i["reached"] and i["detail"] == o["detail"]
            verdict = "PASS" if ok else "FAIL"
        elif o.get("absent") and i.get("absent", not i["reached"]):
            verdict = "NOT PRESENT"
        elif not o["reached"]:
            verdict = "NO CONTROL"
        elif i["reached"]:
            verdict = "FAIL"
        else:
            verdict = "PASS"
        {"PASS": passes, "FAIL": fails}.get(verdict, nocontrol).append(name.split()[0])
        print(f"[{verdict:^11}] {name}")
        print(f"              outside: {'REACHED ' if o['reached'] else 'blocked '} {o['detail']}")
        print(f"              inside : {'REACHED ' if i['reached'] else 'blocked '} {i['detail']}")

    iface_ok = only_loopback_and_down(inside["_ifaces"])
    print(f"[{'PASS' if iface_ok else 'FAIL':^11}] P13 inside has only a loopback interface, and it is down")
    (passes if iface_ok else fails).append("P13")

    print("=" * 100)
    print(f"passed: {' '.join(passes)}")
    print(f"not passed (no positive control / not present): {' '.join(nocontrol) or '-'}")
    print(f"failed: {' '.join(fails) or '-'}")
    if fails:
        print(f"VERDICT: FAIL -- {', '.join(fails)}")
        return 1
    print(f"VERDICT: PASS on every probe with a positive control ({len(passes)}); "
          f"{len(nocontrol)} had nothing reachable even from outside -- listed above, judge separately")
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "inside":
        inside_main(json.loads(sys.argv[2]))
    elif sys.argv[1] == "interfaces-check":
        # The P13 check on its own, against whatever namespace this process is
        # in. On the machine it must print FAIL (the positive control); inside
        # the sandbox it must print PASS.
        ifaces = interfaces()
        print(f"uid={os.getuid()} interfaces: {ifaces['detail']}")
        print(f"P13 only-loopback-and-down: {'PASS' if only_loopback_and_down(ifaces) else 'FAIL'}")
    elif sys.argv[1] == "run":
        sys.exit(main_run(sys.argv[2]))
