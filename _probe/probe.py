#!/usr/bin/env python3
"""临时探针 v3：按固件将要发出的请求原样实测行情接口（用完即删）。

检查项：TLS 1.2 + AES-GCM 能否握手、证书链根、固件请求头是否被接受、
Naver 同一连接上连续两个请求（keep-alive）、响应头与正文。
"""
import base64
import json
import socket
import ssl
import subprocess
import sys
import time

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
ESP_UA = "ESP32 HTTP Client/1.0"
SINA_REFERER = "https://finance.sina.com.cn/"
# mbedTLS 在本固件配置下可用的套件：TLS 1.2、ECDHE、AES-GCM（没有 ChaCha20）。
CIPHERS = ("ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:"
           "ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384")

TENCENT = ("qt.gtimg.cn", "/q=sh000001,sz399001,sz399006,hkHSI,hkHSTECH,"
           "usDJI,usIXIC,usINX,gzN225,gzKS11,gzKOSDAQ")
SINA = ("hq.sinajs.cn", "/list=rt_hkHSI,rt_hkHSTECH")
NAVER_KR = ("polling.finance.naver.com", "/api/realtime/domestic/index/KOSPI,KOSDAQ")
NAVER_JP = ("polling.finance.naver.com", "/api/realtime/worldstock/index/.N225,.TOPX")


def connect(host, tls12_only=True):
    ctx = ssl.create_default_context()
    if tls12_only:
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        ctx.set_ciphers(CIPHERS)
    raw = socket.create_connection((host, 443), timeout=12)
    tls = ctx.wrap_socket(raw, server_hostname=host)
    return tls


def read_response(tls):
    """最小的 HTTP/1.1 响应读取器：支持 Content-Length 与 chunked。"""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = tls.recv(4096)
        if not chunk:
            raise RuntimeError("connection closed before headers")
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1])
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers.setdefault(key.strip().lower(), []).append(value.strip())

    def need(n):
        nonlocal rest
        while len(rest) < n:
            chunk = tls.recv(4096)
            if not chunk:
                raise RuntimeError("connection closed mid-body")
            rest += chunk

    if "chunked" in ",".join(headers.get("transfer-encoding", [])).lower():
        body = b""
        while True:
            while b"\r\n" not in rest:
                need(len(rest) + 1)
            size_line, rest = rest.split(b"\r\n", 1)
            size = int(size_line.split(b";")[0], 16)
            need(size + 2)
            body += rest[:size]
            rest = rest[size + 2:]
            if size == 0:
                break
    elif "content-length" in headers:
        length = int(headers["content-length"][0])
        need(length)
        body, rest = rest[:length], rest[length:]
    else:
        body = rest
        while True:
            chunk = tls.recv(4096)
            if not chunk:
                break
            body += chunk
        rest = b""
    return status, headers, body, rest


def request_bytes(host, path, ua, referer=None, accept_encoding="identity"):
    lines = [f"GET {path} HTTP/1.1", f"User-Agent: {ua}", f"Host: {host}", "Accept: */*"]
    if accept_encoding:
        lines.append(f"Accept-Encoding: {accept_encoding}")
    if referer:
        lines.append(f"Referer: {referer}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def summarize(status, headers, body):
    keep = ("content-type", "content-length", "transfer-encoding", "content-encoding",
            "connection", "keep-alive", "server", "cache-control")
    return {
        "status": status,
        "headers": {key: headers[key] for key in keep if key in headers},
        "header_bytes": sum(len(k) + sum(len(v) for v in vs) for k, vs in headers.items()),
        "bytes": len(body),
        "body_b64": base64.b64encode(body[:6000]).decode("ascii"),
    }


def probe_single(name, target, ua=UA, referer=None, accept_encoding="identity",
                 tls12_only=True):
    host, path = target
    out = {"name": name, "host": host, "path": path, "ua": ua[:24], "referer": bool(referer)}
    started = time.time()
    try:
        tls = connect(host, tls12_only)
        out["tls"] = {"version": tls.version(), "cipher": tls.cipher()[0]}
        out["handshake_ms"] = int((time.time() - started) * 1000)
        request = request_bytes(host, path, ua, referer, accept_encoding)
        out["request_bytes"] = len(request)
        tls.sendall(request)
        status, headers, body, _rest = read_response(tls)
        out.update(summarize(status, headers, body))
        tls.close()
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["ms"] = int((time.time() - started) * 1000)
    return out


def probe_keepalive(name, first, second, pause=0.0):
    """同一条 TLS 连接上连续两个请求：固件对 Naver 的两个地址这样做。"""
    host = first[0]
    out = {"name": name, "host": host, "pause": pause}
    started = time.time()
    try:
        tls = connect(host)
        out["tls"] = {"version": tls.version(), "cipher": tls.cipher()[0]}
        tls.sendall(request_bytes(host, first[1], UA))
        status, headers, body, rest = read_response(tls)
        out["first"] = summarize(status, headers, body)
        out["leftover_after_first"] = len(rest)
        if pause:
            time.sleep(pause)
        tls.sendall(request_bytes(host, second[1], UA))
        status, headers, body, rest = read_response(tls)
        out["second"] = summarize(status, headers, body)
        tls.close()
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["ms"] = int((time.time() - started) * 1000)
    return out


def cert_chain(host):
    """openssl s_client 取服务器发来的证书链，逐张列出 subject / issuer。"""
    out = {"name": "chain", "host": host}
    try:
        shown = subprocess.run(
            ["openssl", "s_client", "-connect", f"{host}:443", "-servername", host,
             "-showcerts", "-tls1_2"],
            input=b"", capture_output=True, timeout=25).stdout.decode("latin-1")
        certs = []
        block = []
        inside = False
        for line in shown.splitlines():
            if "BEGIN CERTIFICATE" in line:
                inside = True
                block = []
            if inside:
                block.append(line)
            if "END CERTIFICATE" in line:
                inside = False
                pem = "\n".join(block) + "\n"
                info = subprocess.run(
                    ["openssl", "x509", "-noout", "-subject", "-issuer", "-enddate",
                     "-fingerprint", "-sha256", "-nameopt", "RFC2253"],
                    input=pem.encode("ascii"), capture_output=True, timeout=10,
                ).stdout.decode("utf-8", "replace").strip().splitlines()
                key = subprocess.run(
                    ["openssl", "x509", "-noout", "-text"],
                    input=pem.encode("ascii"), capture_output=True, timeout=10,
                ).stdout.decode("utf-8", "replace")
                algo = [l.strip() for l in key.splitlines()
                        if "Public Key Algorithm" in l or "Public-Key:" in l
                        or "Signature Algorithm" in l][:3]
                certs.append({"info": info, "key": algo})
        out["certs"] = certs
        out["verify"] = [l.strip() for l in shown.splitlines()
                         if "Verify return code" in l or "Verification" in l][:2]
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def main():
    results = []
    # 1. 固件将要发出的四个请求（TLS 1.2 + AES-GCM，浏览器 UA，identity）。
    results.append(probe_single("fw_tencent", TENCENT))
    results.append(probe_single("fw_sina", SINA, referer=SINA_REFERER))
    results.append(probe_keepalive("fw_naver_keepalive", NAVER_KR, NAVER_JP))
    results.append(probe_keepalive("fw_naver_keepalive_pause2s", NAVER_KR, NAVER_JP, pause=2.0))
    results.append(probe_keepalive("fw_tencent_keepalive", TENCENT, TENCENT))
    # 2. 请求头的必要性：ESP 默认 UA、新浪不带 Referer、不写 Accept-Encoding。
    results.append(probe_single("espua_tencent", TENCENT, ua=ESP_UA))
    results.append(probe_single("espua_sina", SINA, ua=ESP_UA, referer=SINA_REFERER))
    results.append(probe_single("espua_naver_kr", NAVER_KR, ua=ESP_UA))
    results.append(probe_single("espua_naver_jp", NAVER_JP, ua=ESP_UA))
    results.append(probe_single("sina_no_referer", SINA))
    results.append(probe_single("tencent_no_ae", TENCENT, accept_encoding=None))
    results.append(probe_single("naver_no_ae", NAVER_KR, accept_encoding=None))
    results.append(probe_single("naver_gzip", NAVER_KR, accept_encoding="gzip"))
    # 3. 不限制 TLS 版本时服务器协商到什么。
    for name, target in (("tls_any_tencent", TENCENT), ("tls_any_sina", SINA),
                         ("tls_any_naver", NAVER_KR)):
        item = probe_single(name, target,
                            referer=SINA_REFERER if target is SINA else None,
                            tls12_only=False)
        item.pop("body_b64", None)
        results.append(item)
    # 4. 证书链。
    for host in ("qt.gtimg.cn", "hq.sinajs.cn", "polling.finance.naver.com",
                 "raw.githubusercontent.com"):
        results.append(cert_chain(host))
    json.dump({"generated": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
               "results": results}, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
