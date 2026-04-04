#!/usr/bin/env python3
"""
Generate VAPID keypair (P-256) and write to .env in server directory.
This creates VAPID_PUB (base64url no padding) and VAPID_PRI (PEM PKCS8) so the server
can load them and reuse stable keys.

Usage:
    python generate_vapid.py

Caution: regenerating keys invalidates existing subscriptions; clients must re-subscribe.
"""
import base64
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

HERE = Path(__file__).parent
ENV_FILE = HERE / '.env'

# Generate EC key pair on curve SECP256R1 (P-256)
priv = ec.generate_private_key(ec.SECP256R1())
pub = priv.public_key()

# Private key PEM (PKCS8)
priv_pem = priv.private_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PrivateFormat.PKCS8,
    encryption_algorithm=serialization.NoEncryption()
).decode('utf-8')

# Public raw (uncompressed) point -> base64url no padding
pub_raw = pub.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint
)
pub_b64url = base64.urlsafe_b64encode(pub_raw).decode('utf-8').rstrip('=')

# Write to .env (overwrite)
content = []
content.append(f"VAPID_PUB={pub_b64url}")
# store private as PEM (multi-line) - use quoted block marker is not used; we will write literal newlines
content.append('VAPID_PRI=""'
               )
# Build file content carefully: write VAPID_PRI as full PEM on next lines
with open(ENV_FILE, 'w', encoding='utf-8') as f:
    f.write(f"VAPID_PUB={pub_b64url}\n")
    f.write("# VAPID_PRI stored below (PEM). Do not share your private key publicly.\n")
    f.write("VAPID_PRI=-----BEGIN PRIVATE KEY-----\n")
    # PEM contains newlines and trailing newline; write without extra quotes
    for line in priv_pem.strip().splitlines()[1:-1]:
        f.write(line + "\n")
    f.write("-----END PRIVATE KEY-----\n")

print('✅ Generated VAPID keys and wrote to', ENV_FILE)
print('📣 Public key (VAPID_PUB):')
print(pub_b64url)
print('\nImportant: after regenerating keys clients must re-subscribe (press 啟用通知 in the frontend).')
