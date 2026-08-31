# StatLine Acceptable Use Policy

## Effective Date: August 31, 2026

This Acceptable Use Policy (**“AUP”**) governs use of StatLine’s hosted services, SLAPI, Organization Profiles, SADE services, credentials, integrations, and other managed functionality. It is incorporated into the StatLine Terms of Service.

Terms defined in the Terms of Service have the same meaning here.

## 1. Use Only Authorized Access

Use the Service only through credentials, clients, Organization Profiles, scopes, devices, and methods that you are authorized to use.

You may not:

* bypass or attempt to bypass authentication, authorization, device proof, Profile restrictions, scopes, plan limits, or other access controls;
* use another Organization’s Profile, credentials, Device Credential, API Key, enrollment state, or managed entitlement without authorization;
* alter or impersonate an Organization Profile to obtain functionality or managed resources not provisioned to you;
* use a public Adapter as a pretext to bypass restrictions on a separately managed or non-public service resource; or
* access, attempt to access, or cause a client to access non-public administrative, security, credential, or infrastructure resources without authorization.

Public availability of an Adapter or other Open Source Software does not create permission to bypass hosted-Service controls.

## 2. Credentials and Secondary Clients

Keep Enrollment Tokens, API Keys, Device Credentials, private keys, signing material, and other authentication information secure.

Without express authorization, you may not use SADE, a custom client, secondary client, modified client, proxy, browser extension, script, automation, packet capture, social-engineering technique, or other intermediary to:

* obtain or attempt to obtain credentials that were not issued to you;
* solicit or trick another person or client into revealing a credential;
* intercept, capture, harvest, enumerate, derive, validate, replay, exfiltrate, or expose authentication material;
* cause credentials to be sent to an unauthorized destination;
* test whether stolen, guessed, leaked, or third-party credentials are valid; or
* evade a credential revocation, device revocation, or other access restriction.

If you believe a StatLine credential has been exposed, stop using the affected credential and report the issue to **[support@statline.dev](mailto:support@statline.dev)**.

## 3. Security Probing and Sensitive Resources

Unauthorized security testing is prohibited.

Without prior written authorization from StatLine, you may not scan, enumerate, probe, fuzz, exploit, or systematically request sensitive or hidden resources in an attempt to discover secrets, credentials, configuration, backups, administrative interfaces, internal metadata, source-control data, or other non-public material.

Examples of prohibited targets include, without limitation:

* `.env` files or environment dumps;
* cloud-provider credential files or credential directories;
* private keys, token files, secret stores, or authentication databases;
* configuration backups, temporary copies, editor backups, database dumps, or archive files;
* source-control metadata or private repository material;
* undocumented administrative, debug, internal, metadata, or management endpoints; and
* any similarly sensitive path or resource that a reasonable person would understand is not intended for public access.

A request may violate this AUP even if the target file or resource does not exist, is not exposed, returns `404`, `403`, another error, or contains no useful information.

You may not exploit error messages, timing differences, status codes, cache behavior, redirects, or other responses to conduct unauthorized credential or infrastructure discovery.

## 4. No Attacks, Exploitation, or Service Interference

You may not:

* introduce malware, malicious payloads, or destructive data;
* exploit or attempt to exploit a vulnerability without authorization;
* conduct denial-of-service, load, stress, volumetric, or resource-exhaustion testing without written approval;
* perform credential stuffing, password spraying, token guessing, replay attacks, nonce abuse, signature abuse, or similar attacks;
* attempt SSRF, RCE, path traversal, injection, deserialization attacks, file disclosure, privilege escalation, or other unauthorized exploitation;
* interfere with logging, auditing, security controls, revocation systems, or abuse detection;
* intentionally degrade the Service or another user’s access; or
* use the Service to attack, scan, or compromise a third party.

## 5. Rate Limits, Automation, and Fair Use

You must respect plan-specific and endpoint-specific usage limits, including any rate, concurrency, request-size, batch-size, dataset, or other technical limits stated in Documentation, response headers, an Organization Profile, or an applicable order.

Do not:

* create throwaway accounts, rotate keys, rotate devices, rotate IP addresses, or use multiple Organizations to evade usage limits;
* intentionally defeat caching, retry controls, or metering to increase throughput;
* hot-loop or continuously poll endpoints where a reasonable interval or event-driven method is available;
* ignore `429`, `5xx`, retry, backoff, or similar Service guidance; or
* submit malformed or intentionally pathological payloads designed to consume disproportionate resources.

Automated clients should use reasonable retries and backoff and should pin or control dependency versions where appropriate.

## 6. Profiles, Adapters, StatPacks, and Managed Entitlements

Organization Profiles are intended to curate and configure an Organization-specific StatLine experience, including the Adapters, StatPacks, Score Profiles, defaults, integrations, and paid features used by supported clients such as SADE.

Do not manipulate Profile identifiers, guild mappings, configuration, client state, API parameters, or other mechanisms to obtain another Organization’s managed functionality or to use paid features outside the scope provisioned to you.

Standard Organization Adapters may be public and open source. You may lawfully use public Adapter source under its applicable license. However, public Adapter availability does not authorize access to another Organization’s credentials, Profile, paid service, private configuration, or restricted Content.

If you request standard Adapter development through StatLine, do not submit proprietary, confidential, or third-party material for inclusion in a public Adapter unless you have the right to authorize its public use.

Requests for private, exclusive, proprietary, or differently licensed Adapter treatment must be arranged separately as a Special Action.

## 7. Data, Privacy, and Third-Party Rights

Do not submit or process Content that you do not have the right to use.

You may not use the Service to:

* unlawfully collect, disclose, deanonymize, stalk, harass, or profile a person;
* process personal information in violation of applicable privacy, publicity, contractual, or data-protection rights;
* infringe intellectual-property, database, confidentiality, or contractual rights;
* submit malicious, fraudulent, deceptive, or intentionally corrupted datasets; or
* use a third-party data source in violation of access restrictions or law.

Do not submit sensitive personal information unless it is reasonably necessary for an authorized use and you have a lawful basis and any required permissions to do so.

## 8. Rankings, Branding, and Misrepresentation

An Organization may call StatLine-generated rankings, ratings, awards, or outputs “official” for a league, competition, team, or program that the Organization operates or is authorized to administer.

You may not falsely claim that StatLine LLC independently sponsors, certifies, sanctions, governs, or endorses an Organization, competition, player, award, ranking, or outcome without written authorization.

You may not use StatLine trademarks, logos, or branding in a misleading manner or in violation of StatLine’s Trademark Policy.

## 9. Illegal and Regulated Uses

Do not use the Service to violate applicable law or facilitate unlawful conduct.

Use involving regulated gambling, wagering, financial products, sanctions-restricted activity, or other regulated activity is prohibited unless the user and Organization have all required legal authority, licenses, controls, and StatLine approval where reasonably required.

## 10. Security Research and Responsible Disclosure

Security testing of StatLine infrastructure, hosted services, credentials, clients, or integrations requires **prior written authorization** from StatLine unless StatLine publishes a separate program that expressly authorizes the specific testing.

A public source-code license is not authorization to test StatLine’s production infrastructure or other users.

To report a suspected vulnerability without testing beyond what is necessary to identify the issue, contact **[support@statline.dev](mailto:support@statline.dev)**. Do not publicly disclose non-public security details before reasonable coordination with StatLine.

## 11. Security Logging and Investigation

StatLine may record and review operational and security metadata through application logs, authentication audit records, hosting or reverse-proxy logs, firewall systems, and other security tools.

Depending on the system involved, records may include source IP address, timestamp, user agent, method, requested path or resource, HTTP status, Organization, account or device identifier, API-key prefix, authentication state, and security-event details.

These records may be used to detect and investigate conduct prohibited by this AUP, including attempted access to sensitive resources, credential-acquisition attempts, authentication bypass, and enforcement evasion. Data handling is described in the Privacy Policy.

## 12. Enforcement

StatLine may respond to suspected or confirmed violations by taking one or more measures, including:

* rejecting or throttling requests;
* rotating or revoking Enrollment Tokens or API Keys;
* unenrolling, suspending, or revoking devices;
* restricting an Organization Profile or paid feature;
* suspending or terminating an account, Organization, SADE integration, or Subscription;
* blocking an IP address, subnet, network source, client, device, or provider;
* preserving relevant records for investigation or legal purposes;
* notifying an affected customer, infrastructure provider, platform, or law-enforcement authority where appropriate or legally required; and
* pursuing legal remedies.

StatLine may impose an **indefinite or permanent block** for serious, deliberate, repeated, or security-related abuse where reasonably necessary and permitted by law.

## 13. No Enforcement Evasion

There is no permission to work around an enforcement action.

If StatLine blocks or revokes your access, you may not evade that restriction through alternate credentials, new accounts, throwaway accounts, alternate devices, proxies, VPNs, IP rotation, secondary clients, modified clients, another Organization, another person’s credentials, or another technical or organizational workaround.

Attempted evasion is a separate violation and may result in broader enforcement against related accounts, devices, credentials, Organizations, or network sources.

## 14. Changes and Contact

StatLine may update this AUP as security threats, Service functionality, and abuse patterns change. Material updates will be posted with a revised Effective Date.

Questions, security reports, and requests for written testing authorization should be sent to **[support@statline.dev](mailto:support@statline.dev)**.
