# 1) Terms of Service (ToS)

## 1. Acceptance

By obtaining or using a **REGKEY** or **API Access Token**, installing SDKs, or accessing the Service, you accept these Terms. If you use the Service for an organization, you represent you have authority to bind it; "you" includes that organization.

## 2. Key Concepts

* **REGKEY** – A registration key issued by Company that governs access and plan scope.
* **API Access Token** – A credential that corresponds to and is governed by an active REGKEY.
* **Adapter** – A versioned spec/config enabling PRI scoring for a dataset or title.
* **PRI** – Player Rating Index produced by SLAPI. Current normalized range: **55–99**.
* **Content** – Data you submit to or retrieve from the Service (including inputs, outputs, logs).
* **Documentation** – Official docs, policies, and examples we publish.

## 3. Eligibility & Compliance

* You must be **13+**; if under 18, you confirm parental/guardian consent.
* You are responsible for compliance with local laws. **No use for regulated gambling** or other regulated activity without all required licenses and compliance controls.
* Prohibited jurisdictions and sanctioned entities may not use the Service.

## 4. Accounts, Credentials & Security

* Keep REGKEYs/tokens confidential. You are responsible for all activity under your credentials.
* Keys are **non-transferable**; scope may be limited to an org, project, or environment.
* Suspected compromise: notify **[security@statline.app](mailto:security@statline.app)** immediately.
* We may require re-verification, rotate keys, or impose additional controls for risk.

## 5. License to Use the Service

We grant you a **limited, revocable, non-exclusive, non-transferable** license to access and use the Service per these Terms and Documentation. We (and our licensors) retain all rights in the Service, adapters, SDKs, and Documentation. You retain all rights to your **Content**; you grant us a worldwide, royalty‑free license to **process** Content to provide, secure, and improve the Service (including quality, analytics, and abuse prevention).

## 6. Acceptable Use

You may not: (a) bypass auth, rate limits, or metering; (b) share/sublicense keys; (c) scrape/hammer endpoints or build a shadow dataset from outputs in violation of limits; (d) reverse engineer or attempt to extract non-public models/schemas; (e) upload unlawful, infringing, or privacy‑violating data; (f) use the Service to facilitate illegal gambling or any regulated activity without licenses; (g) introduce malware, conduct disruptive testing, or attack the Service; (h) misrepresent outputs as official rankings or endorsements. See **AUP** below (incorporated herein).

## 7. Rate Limits, Fair Use & Caching

Default limit: **60 requests/min per key** (unless otherwise stated). We may throttle, queue, or reject requests that degrade stability. Respect cache headers; do not use caching to evade plan limits.

## 8. Revocation, Suspension & Termination

We may suspend or revoke any REGKEY or token at any time for violations, risk, non‑payment, legal requests, or platform integrity. **Revocation immediately invalidates the REGKEY and all associated API tokens.** You may terminate at any time by ceasing use and deleting keys. Sections intended to survive (e.g., §§5, 10–16, 18–23) remain in effect.

## 9. Changes & Deprecations

We continuously improve the Service and may modify or discontinue features, adapters, or endpoints. Where feasible, we provide **reasonable notice** for materially breaking changes and follow versioning/deprecation windows (see Annex). Your continued use after an update constitutes acceptance.

## 10. Data, Logs & Privacy

We process operational metadata (timestamps, IP, UA, request metrics, error codes) and minimal request fragments for debugging/quality/security. Details are in the **Privacy Policy**. We do **not** sell personal data.

## 11. Outputs & Responsibility

PRI and other outputs are **estimates** derived from your inputs and adapters. We make **no guarantees** of accuracy, suitability, or compliance with third‑party rules. You are responsible for how you use outputs.

## 12. Third‑Party Software & Open Source

SDKs and the Service may include third‑party components licensed under their terms. You agree to those where applicable; notices available upon request.

## 13. Fees & Taxes (if applicable)

You agree to pay applicable fees and taxes under your plan. Failure to pay may result in suspension/revocation. Except as required by law, fees are **non‑refundable**.

## 14. Support, Uptime & Beta

Unless covered by a written SLA, the Service is provided **without uptime commitments**. Beta/preview features may change or be removed without notice. Support is best‑effort via docs, community, or email.

## 15. Security; Vulnerability Disclosure

We employ reasonable technical and organizational measures. Report vulnerabilities to **[security@statline.app](mailto:security@statline.app)**; do not publicly disclose without coordination.

## 16. IP; Trademarks; Feedback

Our trademarks and branding (including **StatLine**, **SLAPI**, **SLcord**) may not be used without permission. If you provide **Feedback**, you grant us a perpetual, irrevocable, royalty‑free license to use it without restriction.

## 17. Export & Government Use

You will comply with U.S. and international export control/sanctions laws. The Service is "Commercial Computer Software" and related documentation provided with only those rights specified herein.

## 18. Indemnification

You will defend, indemnify, and hold harmless Company from third‑party claims arising from your Content, use, or violation of these Terms or the AUP.

## 19. Disclaimers

THE SERVICE IS PROVIDED **“AS IS”** AND **“AS AVAILABLE”** WITHOUT WARRANTIES OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING **MERCHANTABILITY**, **FITNESS FOR A PARTICULAR PURPOSE**, **NON‑INFRINGEMENT**, OR **ACCURACY**. WE DO NOT WARRANT UNINTERRUPTED OR ERROR‑FREE OPERATION.

## 20. Limitation of Liability

TO THE MAXIMUM EXTENT PERMITTED BY LAW: (A) WE ARE NOT LIABLE FOR **INDIRECT, SPECIAL, INCIDENTAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE** DAMAGES OR LOST PROFITS/GOODWILL/DATA; (B) OUR TOTAL AGGREGATE LIABILITY IS LIMITED TO THE **AMOUNTS YOU PAID IN THE 12 MONTHS** PRIOR TO THE EVENT OR **\$100**, WHICHEVER IS GREATER.

## 21. Dispute Resolution; Class Waiver; Venue

**Arbitration:** Any dispute shall be resolved by **binding arbitration** administered by the **AAA** under its rules. Venue: **Marion County, Indiana, USA**. You and Company **waive jury trial** and agree disputes are brought **individually** (no class/collective/representative actions). If arbitration is unenforceable, exclusive jurisdiction lies in the state and federal courts of Marion County, Indiana.

## 22. Notices

We may provide notices via email, dashboard, or website. Legal notices to Company: **[legal@statline.app](mailto:legal@statline.app)** and our posted mailing address.

## 23. Miscellaneous

No waiver unless in writing. If a provision is unenforceable, the remainder remains effective. You may not assign without consent; we may assign to an affiliate/successor. These Terms, the Privacy Policy, Documentation, and any order form/SLA form the **entire agreement**.

### Annex — API & Adapter Operational Terms

1. **PRI Scale:** Normalized 55–99; any change is versioned at the adapter level.
2. **Versioning:** Semantic where feasible (`MAJOR.MINOR.PATCH`). Breaking changes in MAJOR; deprecations target **≥60 days** notice unless security requires faster action.
3. **Filters & Output Toggles:** Availability may vary by adapter; documented per adapter schema.
4. **Revocation Mechanics:** Revocation immediately invalidates the REGKEY and all derived tokens; clients must handle 401/403 and rotate.
5. **SDKs (incl. SLcord):** Provided “as is.” Pin versions, implement retries with jittered backoff, and respect rate‑limit headers.

## One‑Page Summary (Non‑Binding)

* Don’t share keys or bypass limits; violations lead to **fast revocation**.
* We process minimal data to run, secure, and improve the Service; we don’t sell personal data.
* Outputs are estimates; you own your inputs/outputs; our liability is capped.
* Disputes go to AAA arbitration in Marion County, IN; no class actions.
* We can update features and policies; we’ll post effective dates and reasonable deprecations.
