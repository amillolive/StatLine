# StatLine Terms of Service

## Effective Date: August 31, 2026

These Terms of Service (the **“Terms”**) govern access to and use of the hosted StatLine services, SLAPI, managed integrations, Organization Profiles, SADE services, credentials, support, and other commercial services provided by **StatLine LLC** (**“StatLine,” “we,” “us,” or “our”**).

Certain StatLine software is separately available as open-source software. Open-source rights are governed by the applicable open-source license and are not reduced by these Terms.

## 1. Acceptance and Authority

By creating or using an account, redeeming an Enrollment Token, requesting or using an API Access Token, using an Organization Profile, using SADE, purchasing a Subscription Plan, or otherwise accessing the hosted Service, you agree to these Terms and the incorporated Acceptable Use Policy and Privacy Policy.

If you use the Service for an organization, league, team, company, association, or other entity, you represent that you have authority to act for that entity. The entity is the **Organization** for purposes of these Terms.

Organization administrators and persons entering into paid agreements must be at least 18 years old or the age of legal majority where they live. Other users must be legally permitted to use the Service and, if under the age of majority, must have any consent required by applicable law.

## 2. Definitions

For these Terms:

* **Service** means StatLine’s hosted or managed products and services, including SLAPI, hosted scoring, account and authentication services, Organization Profiles, supported integrations, SADE services, and related commercial functionality.
* **Open Source Software** means StatLine software distributed under an open-source license, including software distributed under the GNU Affero General Public License version 3 or later (AGPL-3.0-or-later) where applicable.
* **SLAPI** means StatLine’s hosted or self-operated API layer for authenticated remote access to supported StatLine functionality.
* **Organization** means the customer, league, team, company, association, project, or other entity for which a managed StatLine integration is provisioned.
* **Organization Profile** or **Profile** means the organization-specific configuration used by StatLine and supported clients to determine the Organization’s managed StatLine experience. A Profile may associate or restrict the client-facing use of Adapters, StatPacks, defaults, scoring configurations, integrations, plan features, branding or presentation settings, and other provisioned resources.
* **Score Profile** means a scoring profile, rating profile, or adapter-defined scoring output such as PRI or another scoring variant. A Score Profile is different from an Organization Profile.
* **Adapter** means a versioned StatLine configuration, specification, or implementation used to identify, map, validate, transform, filter, normalize, score, or render a supported dataset, title, league, stat schema, or related statistical source.
* **Organization Adapter** means an Adapter initially developed, commissioned, or materially configured in connection with an Organization’s integration. An Organization Adapter may be described as that Organization’s adapter even though, under the standard StatLine model, it is public and non-exclusive.
* **StatPack** means a StatLine-compatible portable package or bundle containing data, configuration, schemas, mappings, scoring information, metadata, or other supported StatLine material.
* **SADE** means StatLine’s supported Discord-facing client or integration, including successor or replacement versions.
* **Enrollment Token** means a one-time or limited-use registration credential, commonly using a `reg_` prefix, used to initiate an enrollment or registration workflow. An Enrollment Token is not an API Access Token and does not itself guarantee continuing API access.
* **API Access Token** or **API Key** means a credential, commonly using an `api_` prefix, used to authenticate requests to SLAPI or other managed StatLine services.
* **Device Credential** means a device identity, public/private key pair, signed device proof, device file, device identifier, or related credential used by supported StatLine authentication flows. A Device Credential may be copyable and is not necessarily a hardware-bound identity.
* **Subscription Plan** means the recurring paid plan or other commercial entitlement associated with an Organization or user.
* **Implementation Fee** means the one-time fee for initial Organization provisioning and integration work described in Section 8.
* **Special Actions** means non-standard work or commercial treatment that is outside the ordinary implementation or Subscription Plan, including private or exclusive Adapters, proprietary licensing arrangements, dedicated infrastructure, unusual migrations, substantial redevelopment, bespoke security controls, or other custom work quoted separately.
* **Content** means data, datasets, rows, files, StatPacks, commands, configuration, messages, materials, or other information submitted to, processed by, or generated through the Service.
* **Documentation** means official StatLine documentation, schemas, API references, plan descriptions, integration instructions, and policies that we publish.

## 3. Open Source Software and the Hosted Service

Open Source Software is licensed under its applicable open-source license. Nothing in these Terms is intended to revoke, reduce, replace, or impose additional restrictions on rights that an applicable open-source license independently grants to you.

These Terms instead govern the hosted and managed Service, including hosted SLAPI access, credentials, Organization Profiles, managed integrations, SADE services, support, commercial Subscription Plans, and StatLine branding.

Running Open Source Software yourself does not automatically entitle you to hosted SLAPI access, a managed Organization Profile, managed SADE service, support, commercial integrations, or other paid services.

## 4. Organization Profiles

An Organization Profile is the managed configuration layer that makes StatLine operate as an Organization-specific service or client experience.

A Profile may, among other things:

* associate an Organization with relevant Adapters and StatPacks;
* determine which supported Adapters or StatPacks SADE or another managed client displays or uses by default;
* set scoring defaults, Score Profiles, filters, schemas, presentation settings, and integration behavior;
* associate API credentials, service features, and Subscription Plan entitlements with the Organization; and
* provide Organization-specific configuration without making the underlying public software or public Adapter exclusive.

Profile-based filtering or curation does not, by itself, make a public Adapter, public StatPack, or Open Source Software private or exclusive. An Organization may have an Organization-specific client experience while the underlying Adapter remains publicly available.

You may not alter, impersonate, bypass, exploit, or manipulate an Organization Profile to obtain managed functionality, private configuration, credentials, or paid entitlements not provisioned to you or your Organization.

We may change the technical implementation of Profiles over time, including how Profiles are identified, stored, mapped to clients, or enforced, provided that material paid functionality is handled in accordance with the applicable plan or order terms.

## 5. Adapters and the Public Adapter Model

StatLine’s standard Adapter model is public and open.

Unless we expressly agree otherwise in a separate written agreement:

1. an Organization Adapter developed or materially modified as part of a standard StatLine integration may be published in StatLine’s public Adapter catalog, public source repositories, releases, packages, or documentation;
2. the Organization acknowledges that the Organization Adapter is non-exclusive and may be used, studied, modified, redistributed, or incorporated by other users subject to the applicable open-source license;
3. the Organization may accurately refer to the Adapter as its Adapter, an Adapter built for it, an Adapter commissioned by it, or an Adapter supporting its competition, but that description does not create exclusive ownership or access rights;
4. StatLine retains ownership of code, tooling, frameworks, reusable components, implementation techniques, and other materials created by StatLine, subject to the rights granted by applicable open-source licenses;
5. the Organization retains ownership of its pre-existing source data, trademarks, logos, confidential information, and other materials it supplies; and
6. the Organization must not provide confidential, proprietary, licensed, or third-party material for inclusion in a public Adapter unless it has the right to authorize that public use.

Where an Organization or another contributor supplies code or configuration for inclusion in StatLine, that contributor represents that it has sufficient rights to provide the contribution under the applicable project contribution and open-source terms.

A request that an Adapter, Adapter source, scoring implementation, or integration be private, proprietary, exclusive, differently licensed, access-restricted, or withheld from normal public distribution is a **Special Action** and requires a separate written agreement. Additional development, licensing, hosting, maintenance, or exclusivity fees may apply.

Cancellation of a Subscription or deletion of an Organization Profile does not require StatLine or other lawful recipients to remove an Organization Adapter that was already publicly released under an open-source license.

## 6. StatPacks, Datasets, and Scoring Configuration

StatPacks and datasets may be public, Organization-associated, locally supplied, or otherwise handled as described in the applicable Documentation or Organization Profile.

An Organization Profile may limit what StatPacks or datasets are displayed, selected, or used by SADE or another managed client. Such client-facing filtering is an Organization experience and entitlement mechanism. It does not imply that every referenced StatPack or underlying resource is legally private unless StatLine expressly designates it as restricted.

You are responsible for ensuring that you have the rights necessary to submit or cause StatLine to process datasets, StatPacks, league data, player information, and other Content.

## 7. Enrollment, API Keys, and Device Authentication

StatLine may use multiple authentication layers.

An Enrollment Token may be issued for a particular Organization, scope, or enrollment purpose. Enrollment Tokens may expire, may be redeemable only once, and may create a pending request requiring approval before access becomes active.

API Access Tokens are separate credentials used for authenticated Service access. Where a supported authentication flow requires device proof, API access may require both a valid API Access Token and valid proof from an approved Device Credential.

StatLine may approve, deny, expire, rotate, suspend, unenroll, or revoke credentials or devices. A device identity may be represented by a transferable file or cryptographic key and should not be understood as a guaranteed hardware lock.

You must:

* keep Enrollment Tokens, API Keys, Device Credentials, signing material, and other credentials confidential;
* use credentials only for the Organization, user, device, scope, or purpose for which they were issued;
* not publish, sell, sublicense, share, harvest, intercept, replay, or expose credentials except where the Documentation expressly allows a controlled transfer;
* promptly notify StatLine if a credential may have been compromised; and
* comply with credential rotation, re-enrollment, re-verification, and security instructions reasonably required to protect the Service.

Credential possession does not override an Organization Profile, Subscription Plan, scope, device status, revocation status, or other server-side access control.

## 8. Implementation Fee and Initial Organization Setup

Each new paid Organization receiving a standard managed StatLine integration is subject to a one-time **$249.99 Implementation Fee**, unless a different amount or waiver is stated in the applicable order, checkout, invoice, or written agreement.

The Implementation Fee covers the initial technical and administrative work reasonably required to establish the Organization’s managed StatLine implementation, including, as applicable:

* creation and provisioning of the Organization Profile;
* Organization, account, and billing setup;
* API credential and access configuration;
* initial StatPack and Adapter schema configuration;
* association of the Profile with the Organization’s relevant Adapters, StatPacks, Score Profiles, and defaults;
* initial SADE configuration or integration where included in the purchased service; and
* initial development, modification, or publication of one standard Organization Adapter where reasonably required for the agreed integration.

Unless otherwise stated in writing, the standard Implementation Fee covers one initial Organization implementation and one standard data source or Adapter integration. Additional data sources, additional integrations, substantial custom development, or materially different requirements may be quoted separately.

The Implementation Fee is non-refundable once paid, except where a refund is required by law or StatLine determines before performing material implementation work that it cannot provide the agreed setup.

If an Organization cancels and later reactivates the same service, StatLine will not ordinarily charge the Implementation Fee again when the existing Organization Profile and integration can be restored without material redevelopment. A new or additional implementation charge may apply if reactivation requires substantial work because of changed data sources, schemas, third-party services, infrastructure, integration requirements, or requested functionality.

## 9. Subscription Fees, Billing, and Taxes

Recurring Subscription Fees pay for the continuing hosted or managed services identified in the applicable Subscription Plan or order, which may include hosted SLAPI access, API credentials, Organization Profile operation, supported Adapter operation and routine maintenance, SADE operation or integration, and other managed features.

Prices, billing cadence, included usage, and plan-specific features are stated at purchase or in the applicable order or pricing documentation. You agree to pay applicable fees and taxes when due.

Failure to pay may result in suspension of the paid Service, Organization Profile, credentials, SADE integration, or other managed functionality. Except as required by law or expressly stated otherwise, recurring fees already incurred are non-refundable.

Chargebacks, payment disputes, or payment reversals do not eliminate amounts legitimately owed and may result in suspension while the dispute is investigated.

## 10. Service Scope, Usage Limits, and Changes

Usage quotas, rate limits, concurrency limits, payload limits, available endpoints, Adapter availability, StatPack limits, and other technical limits may vary by Subscription Plan, Organization Profile, endpoint, client, or integration. Current limits may be stated in Documentation, response headers, the applicable plan, or an order.

You must comply with documented limits and reasonable technical instructions. StatLine may throttle, queue, reject, or temporarily restrict requests that exceed applicable limits or threaten Service stability or security.

We may add, modify, deprecate, replace, or discontinue features, endpoints, Adapters, StatPacks, clients, authentication methods, or integrations. Where commercially reasonable, we will provide advance notice of materially breaking changes to paid production functionality, except when faster action is reasonably necessary for security, abuse prevention, legal compliance, or third-party platform changes.

## 11. Acceptable Use

The Acceptable Use Policy (**AUP**) is incorporated into these Terms.

Among other restrictions, you may not bypass authentication, evade rate or Profile restrictions, attempt to obtain credentials not issued to you, probe for sensitive resources without authorization, conduct unauthorized security testing, interfere with the Service, or attempt to evade an enforcement action.

## 12. Security Monitoring, Abuse Investigation, and Enforcement

StatLine may use application logging, reverse-proxy or hosting logs, firewall controls, audit records, authentication records, and other security systems to operate and protect the Service.

For security, fraud prevention, debugging, availability, and abuse enforcement, those systems may record information such as:

* timestamps;
* source IP addresses or network information;
* user agents and client identifiers;
* requested methods, routes, paths, resources, or endpoint identifiers;
* resulting HTTP status or response classification;
* Organization, account, API-key prefix, device identifier, scope, or authentication state;
* enrollment, approval, revocation, and authentication events; and
* security signals and investigation notes.

The Privacy Policy describes how such information may be used and retained.

StatLine may investigate suspected abuse and may take measures including warnings, throttling, credential rotation, request rejection, feature restriction, Organization Profile suspension, API-key revocation, device revocation, account suspension, network blocking, IP or subnet blocking, provider-level blocking, or termination.

For serious, deliberate, repeated, or security-related abuse, StatLine may impose an indefinite or permanent block where reasonably necessary and permitted by law.

Attempts to evade an enforcement action through alternate credentials, new accounts, alternate devices, proxy services, VPNs, IP rotation, throwaway accounts, modified clients, secondary clients, or other means are separate violations and may cause additional accounts, credentials, devices, Organizations, or network sources to be blocked.

Nothing in these Terms requires StatLine to disclose detection methods, internal security rules, blocklists, investigation evidence, or sensitive security information to a person whose access has been restricted.

## 13. Unauthorized Credential Acquisition and Security Probing

Without StatLine’s prior written authorization, you may not use the Service, SADE, another client, a secondary or modified client, a proxy, automation, or any other technique to solicit, intercept, derive, capture, replay, exfiltrate, expose, validate, enumerate, or obtain StatLine credentials or authentication material not issued to you.

You also may not systematically probe or request sensitive or hidden infrastructure resources for the purpose of discovering credentials, secrets, configuration, source-control data, backups, administrative interfaces, or other non-public material. Examples include requests targeting `.env` files, cloud-provider credential files, secret files, configuration backups, source-control metadata, private keys, token stores, or similarly sensitive resources.

A failed request is still subject to this section. The fact that a sensitive resource does not exist, is not exposed, or returns an error does not make unauthorized probing permissible.

Security research and testing are governed by the AUP and require prior written authorization unless StatLine publishes a separate security-testing program that expressly permits the activity.

## 14. Content and Data Rights

As between you and StatLine, you retain your rights in Content you lawfully provide, subject to the public Adapter provisions in Section 5 and any separate open-source contribution terms.

You grant StatLine a worldwide, non-exclusive, royalty-free license to host, copy, transmit, transform, map, score, analyze, display, and otherwise process Content as reasonably necessary to provide, maintain, secure, support, and improve the Service and to enforce these Terms.

This license does not give StatLine ownership of your underlying league data, trademarks, or other pre-existing materials.

You represent that you have the rights and permissions necessary for the Content you submit and for the processing you request.

## 15. Rankings, Ratings, and Outputs

StatLine outputs, including PRI and other Score Profiles, are estimates or calculations derived from supplied data, Adapter logic, configuration, and selected methodology. StatLine does not warrant that an output is objectively correct, complete, error-free, suitable for every purpose, or compliant with third-party competition rules.

An Organization may designate StatLine-generated rankings, ratings, awards, or outputs as official for a competition, league, team, or program that the Organization operates or is authorized to administer.

You may not falsely state or imply that StatLine LLC independently sponsors, sanctions, certifies, governs, or endorses an Organization, competition, player, award, ranking, or outcome unless StatLine has expressly agreed to that representation.

## 16. Third-Party Services and Data Sources

The Service may interact with third-party services, APIs, websites, data providers, payment processors, hosting providers, Discord, or other platforms. Your use of those services is also subject to their terms.

StatLine is not responsible for changes, outages, restrictions, data-quality issues, bans, API changes, or other conduct of third parties. Adapter or integration maintenance made necessary by a material third-party change may be treated as routine maintenance or as additional/Special Action work depending on the scope of the change and the applicable Subscription Plan.

## 17. Support, Availability, and Beta Features

Unless a separate written service-level agreement states otherwise, the Service is provided without a guaranteed uptime or response-time commitment. Support is provided on a reasonable best-effort basis through the channels stated in the applicable plan or Documentation.

Beta, preview, experimental, release-candidate, and pre-release features may change, fail, or be discontinued without the notice normally associated with stable production functionality.

## 18. Privacy

StatLine’s Privacy Policy explains how personal information and security/operational information may be collected, used, shared, and retained.

If StatLine processes personal information on behalf of an Organization, the Organization remains responsible for its own legal obligations as controller, business, data owner, or similar role under applicable law, including providing any notices or obtaining any permissions required for the Organization’s collection and submission of data.

## 19. Intellectual Property, Trademarks, and Feedback

Except for rights granted under applicable open-source licenses and rights you retain in your Content, StatLine and its licensors retain all rights in the hosted Service, branding, trademarks, documentation, commercial configuration, and proprietary materials.

Open-source permission to copy or modify software does not automatically grant permission to use StatLine’s trademarks, logos, trade dress, or branding. Trademark use is governed by applicable trademark law and any StatLine Trademark Policy.

If you voluntarily provide ideas, suggestions, or feedback about the Service, you grant StatLine a perpetual, irrevocable, worldwide, royalty-free right to use that feedback without restriction or compensation, provided that this does not transfer ownership of your confidential Content.

## 20. Confidentiality

Each party may receive non-public information from the other that is reasonably understood to be confidential. The receiving party will use reasonable care to protect that information and use it only for the relationship contemplated by these Terms.

Public Open Source Software, publicly released Organization Adapters, public source repositories, public documentation, and information lawfully known without confidentiality obligations are not confidential merely because they relate to an Organization.

If an Organization requires Adapter logic or integration material to remain confidential, that requirement must be agreed as a Special Action before the material is provided for standard public Adapter development.

## 21. Suspension, Cancellation, and Termination

You may stop using the Service or cancel a Subscription subject to the billing terms presented at purchase.

StatLine may suspend or terminate hosted access, an Organization Profile, credentials, devices, integrations, or a Subscription for non-payment, violation of these Terms or the AUP, security risk, legal requirements, abuse, or conduct reasonably likely to harm StatLine, its infrastructure, users, or third parties.

Termination or cancellation may disable managed Profile functionality, API access, or SADE services. Public Open Source Software and publicly released Organization Adapters remain subject to their applicable open-source licenses and are not withdrawn merely because the commercial relationship ends.

Sections that by their nature should survive termination, including provisions concerning public Adapter licensing, accrued fees, security enforcement, intellectual property, disclaimers, limitations of liability, indemnification, dispute resolution, and record retention, survive termination.

## 22. Disclaimer of Warranties

TO THE MAXIMUM EXTENT PERMITTED BY LAW, THE HOSTED SERVICE IS PROVIDED **“AS IS”** AND **“AS AVAILABLE.”** STATLINE DISCLAIMS WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, NON-INFRINGEMENT, ACCURACY, AVAILABILITY, AND ERROR-FREE OPERATION.

STATLINE DOES NOT WARRANT THAT SCORES, RANKINGS, ADAPTERS, STATPACKS, THIRD-PARTY DATA, OR INTEGRATIONS WILL BE COMPLETE, CORRECT, UNINTERRUPTED, OR SUITABLE FOR A PARTICULAR COMPETITION OR DECISION.

Nothing in this section limits rights that cannot lawfully be waived.

## 23. Limitation of Liability

TO THE MAXIMUM EXTENT PERMITTED BY LAW, STATLINE WILL NOT BE LIABLE FOR INDIRECT, INCIDENTAL, SPECIAL, CONSEQUENTIAL, EXEMPLARY, OR PUNITIVE DAMAGES, OR FOR LOST PROFITS, LOST REVENUE, LOST GOODWILL, LOST DATA, OR BUSINESS INTERRUPTION ARISING FROM OR RELATED TO THE SERVICE.

TO THE MAXIMUM EXTENT PERMITTED BY LAW, STATLINE’S TOTAL AGGREGATE LIABILITY ARISING FROM OR RELATED TO THE HOSTED SERVICE OR THESE TERMS WILL NOT EXCEED THE GREATER OF **(A) THE AMOUNT PAID BY THE CLAIMANT TO STATLINE FOR THE APPLICABLE SERVICE DURING THE 12 MONTHS BEFORE THE EVENT GIVING RISE TO THE CLAIM OR (B) $100 USD.**

These limitations apply to the extent permitted by applicable law and do not limit liability that legally cannot be limited.

## 24. Indemnification

To the extent permitted by law, an Organization and any person using the Service on its behalf will defend, indemnify, and hold harmless StatLine and its personnel from third-party claims, damages, and reasonable costs arising from the Organization’s unlawful Content, infringement of third-party rights, unauthorized data submission, violation of these Terms or the AUP, or misuse of the Service.

This section does not require a consumer to indemnify StatLine where such an obligation would be prohibited by applicable law.

## 25. Dispute Resolution; Individual Arbitration; Venue

Before filing a formal claim, the parties will attempt in good faith to resolve the dispute by written notice and at least 30 days of informal discussion, unless urgent injunctive relief is reasonably necessary.

Except for eligible small-claims matters and claims seeking temporary or injunctive relief relating to unauthorized access, security abuse, credential misuse, intellectual-property infringement, or misuse of confidential information, disputes arising from these Terms or the Service will be resolved by binding individual arbitration administered by the American Arbitration Association under the rules applicable to the dispute.

The arbitration will take place in Marion County, Indiana, unless applicable law requires another location or the parties agree otherwise. Each party waives trial by jury to the extent permitted by law.

Claims must be brought individually and not as a plaintiff or class member in a class, collective, consolidated, or representative proceeding to the extent permitted by law.

If a dispute is not subject to arbitration, the parties consent to the exclusive jurisdiction of the state and federal courts located in Marion County, Indiana, except where applicable law requires otherwise.

## 26. Export, Sanctions, and Legal Compliance

You may not use the Service in violation of applicable export-control, sanctions, privacy, intellectual-property, gambling, cybersecurity, computer-access, or other applicable laws.

You may not use the Service on behalf of a sanctioned person or entity where prohibited by law.

## 27. Changes to These Terms

We may update these Terms as the Service changes. Material changes will be posted with an updated Effective Date and, where required or reasonably appropriate, additional notice.

Changes will not retroactively eliminate rights already granted under an open-source license.

If you continue using the hosted Service after revised Terms take effect, the revised Terms apply to future use to the extent permitted by law.

## 28. Notices and Contact

Operational and legal notices may be provided through email, the Service, Documentation, a dashboard, or StatLine’s website.

Questions and legal notices to StatLine may be sent to **[support@statline.dev](mailto:support@statline.dev)** or to any legal mailing address StatLine publishes for that purpose.

## 29. Miscellaneous

These Terms, the Privacy Policy, the AUP, applicable order or checkout terms, and any separate written SLA or Special Action agreement constitute the agreement governing the hosted Service. An applicable open-source license separately governs Open Source Software.

If a provision is unenforceable, the remaining provisions remain in effect to the maximum extent permitted by law. A failure to enforce a provision is not a waiver. You may not assign a paid Organization agreement without StatLine’s consent, except where applicable law provides otherwise. StatLine may assign these Terms in connection with a merger, financing, reorganization, sale of assets, or successor to the Service.
