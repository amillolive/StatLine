# 3) Acceptable Use Policy (AUP)

**This AUP is incorporated into the ToS and governs use of the Service.**

## Prohibited Conduct

1. **Auth/Rate Evasion** – sharing keys, rotating throwaway accounts, or caching tricks to bypass limits.
2. **Abuse** – scraping/hammering, flooding endpoints, or stress-testing without written approval.
3. **Security Violations** – malware, probing, exfiltration attempts, SSRF/RCE, credential stuffing.
4. **Unlawful Content** – infringing, privacy‑violating, or otherwise illegal material.
5. **Regulated Uses** – operating or aiding regulated gambling or other regulated activities without licenses and compliance measures.
6. **Misrepresentation** – presenting outputs as official rankings/endorsements or using our marks without permission.
7. **Data Misuse** – submitting personal data you lack rights to process; ignoring deletion/retention obligations.

## Automation & Fair Usage

* Respect rate limits and retry guidance (jittered backoff on 429/5xx).
* Implement client‑side caching per headers; no cache‑busting to gain throughput.
* Pin SDK versions; avoid hot‑loop polling.
* CSV/batch uploads must be well‑formed and within documented size rows/limits.

## Research & Testing

Security testing requires **written permission**. We support responsible disclosure via **[security@statline.app](mailto:security@statline.app)**.

## Enforcement

We may throttle, suspend, or **revoke** keys; block IPs; or take legal action. We may notify affected parties where legally required.
