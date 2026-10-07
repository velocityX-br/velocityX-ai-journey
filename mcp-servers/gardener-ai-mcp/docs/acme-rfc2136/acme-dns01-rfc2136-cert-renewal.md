# ACME DNS-01 Challenge via RFC2136 in Gardener & Auto-Certificate Renewal

## Overview

Gardener does **not** use cert-manager. It uses its own purpose-built controllers:

| Controller | Repository |
|-----------|-----------|
| `cert-controller-manager` | `gardener/cert-management` |
| `dns-controller-manager` | `gardener/external-dns-management` |

These are exposed to Shoot operators via two extensions:

- `gardener-extension-shoot-cert-service` — manages `Certificate` / `Issuer` CRs
- `gardener-extension-shoot-dns-service` — manages `DNSProvider` / `DNSEntry` CRs

---

## Part 1: How the ACME DNS-01 TXT Record Is Created via RFC2136

### Architecture

```
Shoot cluster
  Ingress (annotated) OR Certificate CR
           │
           ▼
Seed cluster – Shoot control-plane namespace (shoot--<project>--<name>)
  cert-controller-manager
           │ creates
           ▼
  DNSEntry (cert--<domain>)  ← TXT record request
           │
  dns-controller-manager picks up DNSEntry
           │ matches DNSProvider by domains.include
           ▼
  DNSProvider (type: rfc2136) → reads TSIG credentials from Secret
           │
           ▼  RFC 2136 Dynamic DNS Update (TCP/UDP + TSIG auth)
  Authoritative DNS server
  _acme-challenge.<domain> IN TXT "<acme-token>"
           │
           ▼  Let's Encrypt polls and validates
  ACME CA issues certificate
           │
  cert-controller-manager writes Secret → Shoot cluster
           │
  DNSEntry deleted → RFC2136 removes TXT record
```

### Component Namespace Map

| Component | Runs In | Namespace |
|-----------|---------|-----------|
| cert-controller-manager | Seed | Shoot's control-plane namespace |
| dns-controller-manager | Seed | Shoot's control-plane namespace |
| DNSEntry (TXT challenge) | Seed | `shoot--<project>--<name>` |
| DNSProvider (rfc2136) | Seed | `shoot--<project>--<name>` |
| Certificate CR | Shoot | user namespace |
| Result Secret | Shoot | user namespace |

---

### Step-by-Step Flow

#### Step 1 — Trigger a certificate request (Shoot cluster)

**Method A: Ingress annotation**

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-app
  namespace: my-namespace
  annotations:
    cert.gardener.cloud/purpose: managed
    dns.gardener.cloud/dnsnames: "myapp.example.int.sap"
```

**Method B: Explicit Certificate CR**

```yaml
apiVersion: cert.gardener.cloud/v1alpha1
kind: Certificate
metadata:
  name: my-app-cert
  namespace: my-namespace
spec:
  commonName: myapp.example.int.sap
  secretRef:
    name: my-cert-secret
```

#### Step 2 — cert-controller-manager starts the ACME order (Seed)

Contacts the ACME CA (`acme-v02.api.letsencrypt.org`) and selects the `dns-01` solver.
`http-01` and `tls-alpn-01` are not available by default in Gardener.

Log evidence:
```
acme: Could not find solver for: tls-alpn-01
acme: Could not find solver for: http-01
acme: use dns-01 solver
acme: Preparing to solve DNS-01
```

#### Step 3 — DNSEntry created for the challenge (Seed, control-plane namespace)

```yaml
apiVersion: dns.gardener.cloud/v1alpha1
kind: DNSEntry
metadata:
  name: cert--myapp.example.int.sap
  namespace: shoot--myproject--myshoot
spec:
  dnsName: _acme-challenge.myapp.example.int.sap
  ttl: 60
  text:
  - "<acme-token-value>"
```

#### Step 4 — dns-controller-manager matches DNSProvider

Matches by `domains.include` covering the challenged domain, then reads the RFC2136 TSIG credentials from the referenced Secret.

**DNSProvider:**

```yaml
apiVersion: dns.gardener.cloud/v1alpha1
kind: DNSProvider
metadata:
  name: rfc2136-shoot-dns-service-my-dns-secret
  namespace: shoot--myproject--myshoot
spec:
  type: rfc2136
  domains:
    include:
    - myapp.example.int.sap
    - example.int.sap
  secretRef:
    name: ref-my-dns-secret
    namespace: shoot--myproject--myshoot
```

**Secret structure (RFC2136 credentials):**

```yaml
apiVersion: v1
kind: Secret
type: Opaque
data:
  HOST: <base64-encoded-dns-server-ip-or-hostname>
  PORT: <base64-encoded-port, default 53>
  TSIG_KEY_NAME: <base64-encoded-key-name>
  TSIG_SECRET: <base64-encoded-hmac-key>
  TSIG_SECRET_ALGORITHM: <base64 of "hmac-sha256" or "hmac-sha512">
  ZONE: <base64-encoded-zone-name>
```

#### Step 5 — RFC2136 Dynamic DNS Update sent

The `dns-controller-manager` sends an RFC 2136 Dynamic DNS Update (UDP/TCP) to the authoritative DNS server, authenticated via TSIG:

- Adds: `_acme-challenge.<domain> IN TXT "<acme-token>"`
- Zone is updated immediately on the authoritative server.

#### Step 6 — Propagation wait

The `cert-controller-manager` polls until `DNSEntry.status = Ready`, then waits for public DNS propagation before notifying the ACME CA.

Log evidence:
```
dns-challenge-provider: Waiting 5 seconds for DNS entry getting ready [cert--myapp...]...
```

#### Step 7 — ACME CA validates and issues certificate

Let's Encrypt (or other ACME CA) queries public resolvers for `_acme-challenge.<domain>`, confirms the TXT value, and issues the signed certificate chain.

#### Step 8 — Certificate stored in Shoot cluster

`cert-controller-manager` writes the certificate as a Kubernetes `Secret` in the Shoot cluster user namespace.

#### Step 9 — TXT record cleanup

`DNSEntry` is deleted → `dns-controller-manager` issues another RFC2136 update to **remove** the `_acme-challenge` TXT record from the authoritative DNS server.

---

## Part 2: Enabling Automatic Certificate Renewal

Renewal is **fully automatic**. The `cert-controller-manager` continuously monitors certificate expiry and re-triggers the complete DNS-01 flow approximately **30 days before expiry** for 90-day Let's Encrypt certificates (at ~67% of validity). No operator action is required after initial setup.

### Step 1 — Create the RFC2136 TSIG Secret in the Garden project namespace

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: dns-rfc2136-credentials
  namespace: garden-<my-project>
type: Opaque
data:
  HOST: <base64>
  PORT: <base64>
  TSIG_KEY_NAME: <base64>
  TSIG_SECRET: <base64>
  TSIG_SECRET_ALGORITHM: <base64>
  ZONE: <base64>
```

### Step 2 — Enable shoot-dns-service with RFC2136 provider in the Shoot spec

```yaml
kind: Shoot
apiVersion: core.gardener.cloud/v1beta1
spec:
  resources:
    - name: my-rfc2136-secret
      resourceRef:
        apiVersion: v1
        kind: Secret
        name: dns-rfc2136-credentials   # from Step 1
  extensions:
    - type: shoot-dns-service
      providerConfig:
        apiVersion: service.dns.extensions.gardener.cloud/v1alpha1
        kind: DNSConfig
        providers:
          - secretName: my-rfc2136-secret
            type: rfc2136
            domains:
              include:
                - myapp.example.int.sap
```

### Step 3 — Enable shoot-cert-service in the Shoot spec

```yaml
spec:
  extensions:
    - type: shoot-cert-service
      providerConfig:
        apiVersion: service.cert.extensions.gardener.cloud/v1alpha1
        kind: CertConfig
        shootIssuers:
          enabled: true            # allows Certificate CRs in the Shoot
        issuers:
          - email: your-team@example.com
            name: lets-encrypt-prod
            server: https://acme-v02.api.letsencrypt.org/directory
            # For testing use staging:
            # server: https://acme-staging-v02.api.letsencrypt.org/directory
```

### Step 4 — Annotate your Ingress (simplest path, auto-renewal included)

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: my-app
  namespace: my-namespace
  annotations:
    # Certificate management
    cert.gardener.cloud/purpose: managed
    cert.gardener.cloud/issuer: lets-encrypt-prod
    cert.gardener.cloud/secret-name: my-app-tls
    # DNS management
    dns.gardener.cloud/dnsnames: "myapp.example.int.sap"
    dns.gardener.cloud/ttl: "120"
spec:
  tls:
    - hosts:
        - myapp.example.int.sap
      secretName: my-app-tls
  rules:
    - host: myapp.example.int.sap
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: my-app-svc
                port:
                  number: 80
```

### Step 4 (Alternative) — Explicit Certificate CR

Requires `shootIssuers.enabled: true` in CertConfig (Step 3).

```yaml
apiVersion: cert.gardener.cloud/v1alpha1
kind: Certificate
metadata:
  name: my-app-cert
  namespace: my-namespace
spec:
  commonName: myapp.example.int.sap
  dnsNames:
    - api.example.int.sap
  secretRef:
    name: my-app-tls-secret
    namespace: my-namespace
  issuerRef:
    name: lets-encrypt-prod
    namespace: my-namespace
```

### Step 5 — Verify certificate status

```bash
# Check certificate state in the Shoot cluster
kubectl get certificate my-app-cert -n my-namespace -o yaml
```

Key fields in `.status`:

| Field | Meaning |
|-------|---------|
| `conditions[Ready=True]` | Certificate is valid and in use |
| `expirationDate` | Expiry timestamp; renewal scheduled from this |
| `commonName` | CN of the issued certificate |
| `issuerRef` | Issuer that signed it |
| `message` | Current status / progress message |

---

## Common Failure Modes

| Symptom | Root Cause | Fix |
|---------|-----------|-----|
| `no provider found to apply DNS entry for <domain>` | `DNSProvider.domains.include` does not cover the challenged domain | Add the domain to `domains.include` |
| RFC2136 update rejected / `REFUSED` | TSIG key lacks TXT record write permission in the zone | Grant the key permission to update TXT records in the zone |
| TXT record created but Let's Encrypt can't verify | Authoritative server not publicly accessible or zone delegation broken | Verify the zone is publicly delegated and the server is reachable |
| `DNSEntry` stays in `Pending` forever | No matching `DNSProvider` found | Check `domains.include` alignment |
| ACME rate limit errors | Too many new orders | Set `spec.requestsPerDayQuota` on the `Issuer` object |

---

## Advanced: CNAME Delegation for Challenge Records

For production zones where you don't want to grant the automation write access to the entire zone, use CNAME delegation:

```
_acme-challenge.myapp.example.int.sap  IN CNAME  _acme-challenge.myapp.acme-delegated.example.int.sap
```

The RFC2136 TSIG key then only needs write access to the `acme-delegated.example.int.sap` zone. The `cert-controller-manager` follows CNAMEs automatically.

---

## Relevant Repositories

| Repo | Purpose |
|------|---------|
| `gardener/cert-management` | `cert-controller-manager` — ACME ordering, renewal scheduling |
| `gardener/external-dns-management` | `dns-controller-manager` — RFC2136 Dynamic Update driver |
| `gardener/gardener-extension-shoot-cert-service` | Shoot integration for cert-management |
| `gardener/gardener-extension-shoot-dns-service` | Shoot integration for dns-management |
