---
source_url: https://fims.cosmopolitan.edu.ng/docs
source_title: "FIMS API Documentation | CCSA"
retrieved_at: 2026-09-26
verification: published
content_type: server_rendered
routes:
  - R-FIMS-API
---

# FIMS API Documentation

The Centre for Climate-Smart Agriculture publishes an openly documented HTTP API
for the Farmers Information Management System.

## Connection details

| Property | Value |
|---|---|
| Base URL | https://fims.cosmopolitan.edu.ng |
| API version | v1 |
| Protocol | HTTPS only |
| Format | JSON |
| OpenAPI specification | https://fims.cosmopolitan.edu.ng/api/v1/openapi.json |

## Authentication

API Key, supplied either as a Bearer token or in an `X-API-Key` header.

## Requesting an API key

"To request an API key, submit an access request or email api@cosmopolitan.edu.ng
with your organisation name, intended use-case, and the scopes you require."

Available scopes:

- `farmers:read`
- `farms:read`
- `clusters:read`
- `analytics:read`

## Field-level data classification

The documentation classifies fields by group and marks sensitive values as
redacted:

| Group | Fields | Default |
|---|---|---|
| identity | nin | Redacted |
| financial | bvn, bankName, accountNumber, accountName | Redacted |
| contact | phone, email, whatsAppNumber | Redacted |

## Error codes

| Code | Meaning |
|---|---|
| 404 | Not Found — the requested record does not exist |
| 422 | Unprocessable Entity — invalid parameters |
| 429 | Too Many Requests |
| 500 | Internal Server Error |

## Support

api@cosmopolitan.edu.ng
