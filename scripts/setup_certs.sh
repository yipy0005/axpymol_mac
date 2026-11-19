#!/bin/bash
# Create and trust a localhost certificate (needed: Office add-ins require HTTPS). Prompts for your password.
set -e
D="$HOME/Library/Application Support/AxPyMOL/certs"; mkdir -p "$D"
openssl req -x509 -newkey rsa:2048 -nodes -days 825 -keyout "$D/localhost.key" -out "$D/localhost.crt" \
  -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
  -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,digitalSignature,keyCertSign" \
  -addext "extendedKeyUsage=serverAuth"
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain "$D/localhost.crt"
echo "Certificate installed in $D"
