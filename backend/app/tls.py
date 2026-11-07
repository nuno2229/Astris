"""Local HTTPS (P-6): phones only allow live camera scanning on a secure (https) address. With HTTPS=self-signed
the server makes its own small certificate authority once, and a certificate for the names/addresses you list in
TLS_HOSTNAMES (e.g. "192.168.1.20,tower.local"). Install the CA certificate (/ca.crt) on each phone once.

    python -m app.tls ensure     # create or renew; prints the folder

Kept in TLS_DIR (default: the backups folder's tls/ subfolder, so it survives container updates). The CA key never
leaves the server; only the public CA certificate is downloadable."""
import ipaddress
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

SERVER_DAYS = 397          # browsers reject longer-lived server certificates
RENEW_BEFORE = timedelta(days=30)


def tls_dir() -> Path:
    return Path(os.environ.get("TLS_DIR") or Path(os.environ.get("BACKUP_DIR", "/data/backups")) / "tls")


def hostnames() -> list[str]:
    names = [h.strip() for h in os.environ.get("TLS_HOSTNAMES", "").split(",") if h.strip()]
    return list(dict.fromkeys(names + ["localhost", "127.0.0.1"]))


def _write(path: Path, data: bytes, secret: bool) -> None:
    path.write_bytes(data)
    os.chmod(path, 0o600 if secret else 0o644)


def _san(names: list[str]) -> x509.SubjectAlternativeName:
    out = []
    for n in names:
        try:
            out.append(x509.IPAddress(ipaddress.ip_address(n)))
        except ValueError:
            out.append(x509.DNSName(n))
    return x509.SubjectAlternativeName(out)


def ensure(d: Path | None = None, names: list[str] | None = None) -> Path:
    d = d or tls_dir()
    names = names or hostnames()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    now = datetime.now(UTC)
    ca_key_p, ca_crt_p = d / "ca.key", d / "ca.crt"
    if not (ca_key_p.exists() and ca_crt_p.exists()):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Astris home server CA")])
        ca = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
              .not_valid_after(now + timedelta(days=3650))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                           key_encipherment=False, data_encipherment=False, key_agreement=False,
                                           encipher_only=False, decipher_only=False), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
              .sign(key, hashes.SHA256()))
        _write(ca_key_p, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()), secret=True)
        _write(ca_crt_p, ca.public_bytes(serialization.Encoding.PEM), secret=False)
    ca_key = serialization.load_pem_private_key(ca_key_p.read_bytes(), None)
    ca = x509.load_pem_x509_certificate(ca_crt_p.read_bytes())
    srv_key_p, srv_crt_p = d / "server.key", d / "server.crt"
    if srv_crt_p.exists() and srv_key_p.exists():
        cur = x509.load_pem_x509_certificate(srv_crt_p.read_bytes())
        try:
            have = cur.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound:
            have = None
        if have == _san(names) and cur.not_valid_after_utc - now > RENEW_BEFORE:
            return d
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
            .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=SERVER_DAYS))
            .add_extension(_san(names), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    _write(srv_key_p, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()), secret=True)
    _write(srv_crt_p, cert.public_bytes(serialization.Encoding.PEM) + ca_crt_p.read_bytes(), secret=False)
    return d


if __name__ == "__main__":
    if sys.argv[1:] == ["ensure"]:
        print(ensure())
    else:
        print(__doc__)
        sys.exit(2)
