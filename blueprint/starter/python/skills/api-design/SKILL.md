---
name: api-design
description: Design or review an HTTP API as a contract (resources, status codes, one error shape, pagination,
  idempotent writes, versioning). Use it whenever you add or change an endpoint, a field or an error format, write an
  OpenAPI document, or need to know whether a change breaks clients.
license: MIT
metadata:
  sources: wshobson/plugins/backend-development/skills/api-design-principles, agency-agents/engineering/engineering-api-platform-engineer.md, agency-agents/engineering/engineering-backend-architect.md
---

# API design

Once a client uses an API, its working code defines what you may not break. Design the contract before the code,
decide the conventions once, and check every change for compatibility.

## 1. Model the resources

Start from the nouns of the domain and their life cycle, not from the database tables or the screens. Write them down
with their relations: an order has lines, belongs to a customer, moves from open to paid to shipped. The API mirrors
that model; the storage behind it can change.

## 2. Paths and methods

- Plural nouns for collections, ids for members: `/orders`, `/orders/{order_id}`, `/orders/{order_id}/lines`.
- Nest one level at most; deeper relations get their own top-level path with a filter (`/lines?order_id=…`).
- An action that is not create, read, update or delete becomes a sub-resource or a state change:
  `POST /orders/{order_id}/cancellation`, or `PATCH` with `{"status": "cancelled"}`.

| Method | Use | Safe | Idempotent |
|---|---|---|---|
| `GET` | read | yes | yes |
| `POST` | create, or start an action | no | no (make it so with a key) |
| `PUT` | replace the whole resource | no | yes |
| `PATCH` | change some fields | no | not by default |
| `DELETE` | remove | no | yes |

## 3. Status codes

Use the few that clients act on: `200` read or changed, `201` created (with `Location`), `202` accepted for later,
`204` no content, `400` malformed, `401` not signed in, `403` not allowed, `404` not found, `409` conflict with the
current state, `422` valid JSON but invalid values, `429` too many requests (with `Retry-After`), `500` your bug,
`503` temporarily unavailable. A `200` with an error in the body hides failures from every client and proxy.

## 4. One error shape

Every error, from every endpoint, has the same body. The problem-details format (RFC 9457) is a good default:

```json
{
  "type": "https://example.com/problems/insufficient-stock",
  "title": "Not enough stock",
  "status": 409,
  "detail": "Only 2 of item 'blue-mug' are left; the order asks for 5.",
  "code": "insufficient_stock"
}
```

A stable machine-readable `code` lets clients branch without parsing text; `detail` tells a person what to do.
Validation errors list each field with its problem. No stack traces, SQL or secrets in a response.

## 5. Conventions, decided once

- One naming style for fields (`snake_case` or `camelCase`), everywhere.
- Dates and times in ISO 8601 with a time zone (`2026-11-09T14:30:00Z`).
- Ids as strings in JSON, even when they are numbers in the database, so they can change form later.
- Money as an amount in minor units or a decimal string, with the currency; never a float.
- Collections are paginated from the start. Cursor pagination (`?limit=50&cursor=…`, the response carries
  `next_cursor`) stays correct while rows are added; offset pagination is fine for small, stable lists.
- Filtering and sorting through query parameters with the same names on every collection.

## 6. Writes that can be retried

Networks fail halfway. A client that retries a `POST` must not create two orders. Accept an `Idempotency-Key` header
on creating requests, store the key with the result, and return the stored result for a repeat. `PUT` and `DELETE`
are idempotent by design; keep them so.

## 7. Compatibility

| Safe (additive) | Breaking |
|---|---|
| a new endpoint | removing or renaming an endpoint or a field |
| a new optional request field | a new required request field |
| a new response field | changing a field's type or meaning |
| a new enum value, if clients were told to expect unknown values | a new enum value clients cannot handle |
| a looser validation | a stricter validation, a changed default |
| a new error `code` | a changed status code for the same case |

Make additive changes freely. For a breaking change, add the new form beside the old one, mark the old one deprecated
(`Deprecation` and `Sunset` headers, the changelog, the docs), watch its use, and remove it only after the announced
date. Version in the path (`/v2/`) only when a whole set of resources changes; one changed field does not need a new
version.

## 8. The contract in a file

Keep an OpenAPI document as the contract, generated from the code (FastAPI does) or written first and checked
against it in the tests. Generate clients and frontend types from it rather than writing shapes by hand. A test that
compares the document with the last released one catches a breaking change before it ships.

## 9. Also decide

Authentication (who) and authorisation (what, checked per resource, not only per endpoint), rate limits with
`429` and `Retry-After`, request size limits, and timeouts. Write them down with the conventions.

## Report

For a design or a review:

```
Resources: <nouns and relations>
Endpoints: <method path - purpose - success status - main errors>
Conventions: <naming, dates, ids, pagination, errors>
Compatibility: <safe / breaking, with the migration path for anything breaking>
Open questions: <decisions the user must make>
```
