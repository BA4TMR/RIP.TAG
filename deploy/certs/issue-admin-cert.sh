#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Create a private client CA (first run) and issue one admin client certificate.
#
# Run this on a trusted admin workstation, not on the public server.
# Copy ONLY riptag-client-ca.crt to the server for Nginx ssl_client_certificate.
# Keep riptag-client-ca.key offline.
#
# Usage:
#   P12_PASSWORD='choose-a-password' ./issue-admin-cert.sh main-control
#
# Outputs:
#   certs/riptag-client-ca.crt / .key
#   certs/main-control.key / .crt / .p12
# ---------------------------------------------------------------------------
set -euo pipefail

NAME="${1:-main-control}"
# Deliberately outside the source tree; export secrets must never be committed.
CERT_DIR="${CERT_DIR:-$PWD/riptag-certs}"
P12_PASSWORD="${P12_PASSWORD:-}"
DAYS="${DAYS:-825}"

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl is required" >&2
  exit 1
fi
if [[ -z "$P12_PASSWORD" ]]; then
  echo "Set P12_PASSWORD to protect the exported .p12 file." >&2
  exit 1
fi
if [[ ! "$NAME" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "NAME may only contain letters, digits, dot, underscore and hyphen." >&2
  exit 1
fi

mkdir -p "$CERT_DIR"
chmod 700 "$CERT_DIR"
cd "$CERT_DIR"

if [[ ! -f riptag-client-ca.crt || ! -f riptag-client-ca.key ]]; then
  echo "[1/4] Creating the private client CA"
  openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
    -keyout riptag-client-ca.key \
    -out riptag-client-ca.crt \
    -subj "/CN=RIP TAG Client CA/O=RIP TAG/"
  chmod 600 riptag-client-ca.key
else
  echo "[1/4] Using existing client CA"
fi

if [[ -f "${NAME}.crt" ]]; then
  echo "Refusing to overwrite ${NAME}.crt; choose another NAME or remove it explicitly." >&2
  exit 1
fi

echo "[2/4] Creating client key and CSR for ${NAME}"
openssl req -newkey rsa:3072 -sha256 -nodes \
  -keyout "${NAME}.key" \
  -out "${NAME}.csr" \
  -subj "/CN=${NAME}/O=RIP TAG Client/"

cat > "${NAME}.ext" <<'EOF'
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

echo "[3/4] Signing the client certificate"
openssl x509 -req -sha256 -days "$DAYS" \
  -in "${NAME}.csr" \
  -CA riptag-client-ca.crt \
  -CAkey riptag-client-ca.key \
  -CAcreateserial \
  -out "${NAME}.crt" \
  -extfile "${NAME}.ext"

echo "[4/4] Exporting ${NAME}.p12"
openssl pkcs12 -export \
  -out "${NAME}.p12" \
  -inkey "${NAME}.key" \
  -in "${NAME}.crt" \
  -certfile riptag-client-ca.crt \
  -name "RIP TAG 主控 - ${NAME}" \
  -passout "pass:${P12_PASSWORD}"

chmod 600 "${NAME}.key" "${NAME}.p12"
rm -f "${NAME}.csr" "${NAME}.ext"

echo
echo "Created:"
echo "  CA cert (copy to server): ${CERT_DIR}/riptag-client-ca.crt"
echo "  Client p12 (install on device): ${CERT_DIR}/${NAME}.p12"
echo
echo "Verify:"
echo "  openssl verify -CAfile riptag-client-ca.crt ${NAME}.crt"
echo
echo "Server:"
echo "  install -m 644 riptag-client-ca.crt /etc/nginx/certs/"
echo "  nginx -t && systemctl reload nginx"
