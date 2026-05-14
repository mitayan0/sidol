# Sidol roadmap

Phased delivery: harden one connector, extract only after repetition, add a structurally different second connector, then ship the SDK surface. **Do not design for the third connector while building the first.**

---

## Phase 0 — ServiceNow hardening (current focus)

**Goal:** One connector is **production-quality** before generalizing anything.

**Deliverables**

| Item | Description |
|------|-------------|
| **Paginated fetch** | Table API reads use correct `sysparm_limit` / `sysparm_offset`; honor caller `limit` without over-fetching; stop cleanly at end of data. |
| **`sysparm_query` filter pushdown** | Map Sidol filter dicts to encoded ServiceNow queries; escape `^`; handle `NULL`/empty and `IN` safely where supported. |
| **OAuth 2.0 + token refresh** | Optional client id/secret + refresh token (and optional initial access token); refresh on 401; token endpoint `oauth_token.do`. |
| **Dot-walking** | Caller may pass dotted field names in `columns` (e.g. `caller_id.name`); optional `sysparm_display_value` for reference display values. |
| **Actionable errors** | HTTP failures surface ServiceNow `error.message` / `error.detail`, status, and `X-Correlation-ID` / `x-snc-correlation-id` when present. |

**Out of scope for Phase 0:** `sidol.utils`, `sidol.testing`, `ConnectorContext`, new connectors, `Session` SELECT pushdown.

### Phase 0 execution checklist

Use this list to track implementation work in-repo.

- [x] Roadmap committed (`ROADMAP.md`).
- [x] `fetch()` pagination respects `limit` and uses correct per-page `sysparm_limit`.
- [x] `_build_query()` / filter encoding hardened (`^` escape, null/empty, `IN`).
- [x] OAuth optional params + refresh on 401 + form POST to `oauth_token.do`.
- [x] `sysparm_display_value` optional flag for dot-walk / display fields.
- [x] Shared HTTP error parsing for Table API + OAuth; writes raise `WriteError` where appropriate.
- [x] Unit tests (mocked HTTP) for pagination, query encoding, OAuth refresh, errors.
- [x] `.env.example` documents OAuth variables.

---

## Phase 1 — Extract what repeated itself

**After** ServiceNow is stable: pull repeated logic into **small pure functions** inside [`sidol/connectors/servicenow.py`](sidol/connectors/servicenow.py) (or SN-scoped helpers). **Move to `sidol.utils` only when a second connector needs the same code.**

---

## Phase 2 — Second connector (different API style)

Add **one** connector unlike ServiceNow (e.g. **Notion** or **Airtable**) to validate or redesign shared helpers **before** they become a public contract.

---

## Phase 3 — SDK surface

After **three** connectors you have written yourself (tighten to “three non-trivial adapters” if needed): `sidol.testing`, `ConnectorContext`, richer `Capabilities` + `Session` wiring, `CONNECTOR_GUIDE.md`.

---

## Diagram

```mermaid
flowchart LR
  P0[Phase0_ServiceNow]
  P1[Phase1_ExtractInPlace]
  P2[Phase2_SecondConnector]
  P3[Phase3_SDKSurface]
  P0 --> P1
  P1 --> P2
  P2 --> P3
```
