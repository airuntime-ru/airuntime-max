# Russian Trusted CA

`russian-trusted-ca.crt` holds two public certificates from the Ministry of Digital
Development: **Russian Trusted Root CA** (self-signed) and **Russian Trusted Sub CA**.

## Why it is here

`platform-api2.max.ru` — the MAX Bot API — presents a certificate issued by *Russian
Trusted Sub CA*, which is in no default trust store. Without this file every MAX API call
fails with `CERTIFICATE_VERIFY_FAILED`, so the bot silently never delivers a message.

The backend image installs it into the system trust store (see `backend/Dockerfile`), which
is what `httpx` picks up.

## Fingerprints (SHA-256)

Verify these before trusting a replacement copy:

```
Root CA  D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31
Sub CA   BB:BD:E2:10:3E:79:0B:99:9E:C6:2B:D0:3C:F6:25:A5:A2:E7:C3:16:E1:0A:FE:6A:49:0E:ED:EA:D8:B3:FD:9B
```

Source: <https://gu-st.ru/content/Other/doc/russiantrustedca.pem>

Check the chain actually validates against this file:

```bash
echo | openssl s_client -connect platform-api2.max.ru:443 \
  -servername platform-api2.max.ru -CAfile infra/certs/russian-trusted-ca.crt \
  2>/dev/null | grep "Verify return code"
```

## Scope

This only affects outbound calls to `*.max.ru`. It is added to the container's trust
store rather than passed per-request so that every HTTP client in the process behaves the
same way; nothing else in the stack talks to a host signed by this CA.
