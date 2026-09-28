"""Start the attendance server.

    python run.py                      # http://127.0.0.1:8000 (this computer only)
    python run.py --lan                # https://<this-pc-ip>:8443 for classroom PCs / phones / tablets

Browsers only allow camera access on localhost or over HTTPS, so --lan creates a
self-signed certificate in data/tls/ (first visit: accept the browser warning once,
or install data/tls/cert.pem as trusted on the classroom devices).
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))  # no packet is sent; picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def ensure_certificate(tls_dir: Path, ip: str) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    cert_path, key_path = tls_dir / "cert.pem", tls_dir / "key.pem"
    if cert_path.exists() and key_path.exists():
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if ipaddress.ip_address(ip) in sans.get_values_for_type(x509.IPAddress):
            return cert_path, key_path
    tls_dir.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Smart Classroom Attendance")])
    now = dt.datetime.now(dt.timezone.utc)
    san = x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                                       x509.IPAddress(ipaddress.ip_address(ip)), x509.DNSName(socket.gethostname())])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=825)).add_extension(san, critical=False)
            .sign(key, hashes.SHA256()))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()))
    key_path.chmod(0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lan", action="store_true", help="serve over HTTPS on the local network")
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--cert", type=Path, help="use your own TLS certificate (PEM)")
    ap.add_argument("--key", type=Path, help="private key for --cert")
    args = ap.parse_args()

    import uvicorn

    from app.config import settings
    from app.vision.backends import ANTISPOOF_FILE, SFACE_FILE, YUNET_FILE

    missing = [f for f in (YUNET_FILE, SFACE_FILE) if not (settings.models_dir / f).exists()]
    if missing and settings.vision_backend == "opencv":
        print("Face models missing - downloading them first ...")
        import subprocess
        subprocess.run([sys.executable, str(ROOT / "scripts" / "download_models.py"), "--dir", str(settings.models_dir)])
    if not (settings.models_dir / ANTISPOOF_FILE).exists():
        print("Note: anti-spoofing model not installed - liveness uses the head-movement test.")

    kwargs = {}
    if args.lan or args.cert:
        ip = lan_ip()
        host, port = args.host or "0.0.0.0", args.port or 8443
        cert, key = (args.cert, args.key) if args.cert else ensure_certificate(ROOT / "data" / "tls", ip)
        kwargs = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
        print(f"\n  Open on classroom devices:  https://{ip}:{port}\n")
    else:
        host, port = args.host or "127.0.0.1", args.port or 8000
        print(f"\n  Open:  http://{host}:{port}\n")
    # one worker: live face trackers are kept in process memory
    uvicorn.run("app.main:app", host=host, port=port, workers=1, proxy_headers=True, **kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
