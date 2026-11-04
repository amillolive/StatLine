# RC7 validation

This source patch updates the Python package, generated Gateway contract, portal layout, read-only identity request UX, and operator name. RC6 remains the historical baseline for the original scoring fixtures.

Verified for this uploaded source patch:

- Regeneration completed: 36 API operations, 2 EBA demos, 127 scored results.
- `python portal/scripts/check-generated.py` passed the source fingerprint, API contract, and downloadable example checks.
- `pytest -q --ignore=tests/test_session.py` passed: 252 passed, 3 skipped (the joint-launch tests require a built portal).

Full-suite collection could not complete in this environment: `httpx2` is not installed, which `tests/test_session.py` requires. The TypeScript build, native Windows installer, deployed hostname routing, CORS with live credentials, and live browser testing have not been verified here.

Earlier RC6 fixtures and tests retain their historical RC6 labels; that is not a current release version.
