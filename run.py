"""Start the attendance server.

    python run.py                      # http://127.0.0.1:8000 (this computer only)
    python run.py --lan                # https://<this-pc-ip>:8443 for phones / tablets / classroom PCs

Browsers only allow camera access (and app installation) over HTTPS, so --lan creates a
small local certificate authority in data/tls/. Install it once on each phone from the
"Mobile setup" page (/mobile) and the phone trusts the server; the app can then be
installed from the browser.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import os
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


def _new_key():
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _write_key(path: Path, key) -> None:
    from cryptography.hazmat.primitives import serialization

    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()))
    try:
        path.chmod(0o600)
    except OSError:
        pass


def ensure_certificate(tls_dir: Path, ip: str) -> tuple[Path, Path]:
    """Local certificate authority (installed once on each phone) + a server certificate
    for this PC's current IP address, signed by it. If the IP changes only the server
    certificate is re-issued, so phones keep trusting the server."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    tls_dir.mkdir(parents=True, exist_ok=True)
    ca_cert_p, ca_key_p = tls_dir / "ca.pem", tls_dir / "ca-key.pem"
    cert_p, key_p = tls_dir / "cert.pem", tls_dir / "key.pem"
    now = dt.datetime.now(dt.timezone.utc)

    if ca_cert_p.exists() and ca_key_p.exists():
        ca_cert = x509.load_pem_x509_certificate(ca_cert_p.read_bytes())
        ca_key = serialization.load_pem_private_key(ca_key_p.read_bytes(), password=None)
    else:
        ca_key = _new_key()
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Smart Classroom Attendance CA ({socket.gethostname()})"),
                             x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Smart Classroom Attendance")])
        ca_cert = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
                   .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
                   .not_valid_after(now + dt.timedelta(days=3650))
                   .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                   .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                                content_commitment=False, key_encipherment=False, data_encipherment=False,
                                                key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
                   .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
                   .sign(ca_key, hashes.SHA256()))
        _write_key(ca_key_p, ca_key)
        ca_cert_p.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
        cert_p.unlink(missing_ok=True)

    if cert_p.exists() and key_p.exists():
        cert = x509.load_pem_x509_certificate(cert_p.read_bytes())
        try:
            ips = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)
        except x509.ExtensionNotFound:
            ips = []
        valid = cert.issuer == ca_cert.subject and cert.not_valid_after_utc > now + dt.timedelta(days=7)
        if valid and ipaddress.ip_address(ip) in ips:
            return cert_p, key_p

    key = _new_key()
    names = [x509.DNSName("localhost"), x509.DNSName(socket.gethostname()),
             x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.IPAddress(ipaddress.ip_address(ip))]
    cert = (x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, ip)]))
            .issuer_name(ca_cert.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=800))  # Apple accepts at most 825 days
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write_key(key_p, key)
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM)
                       + ca_cert.public_bytes(serialization.Encoding.PEM))  # full chain
    return cert_p, key_p


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
        os.environ["PUBLIC_URL"] = f"https://{ip}:{port}"
        print(f"\n  On this PC open:        https://localhost:{port}"
              f"\n  On phones / tablets:   https://{ip}:{port}"
              f"\n  Phone setup + QR code: https://localhost:{port}/mobile\n")
    else:
        host, port = args.host or "127.0.0.1", args.port or 8000
        print(f"\n  Open:  http://{host}:{port}\n")
    # one worker: live face trackers are kept in process memory
    uvicorn.run("app.main:app", host=host, port=port, workers=1, proxy_headers=True, **kwargs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
