# Security

## Reporting a vulnerability

Report a vulnerability privately through [GitHub security advisories](https://github.com/Mitroshenkov87/voxprint-common/security/advisories/new).

Please include the version (`voxprint_common.__version__`), the operating system, and a short way to reproduce the issue. Do not open a public issue for an unfixed vulnerability.

## Scope

voxprint-common reads and writes the suite directory, takes a cross-process GPU lock, and downloads files the caller names over `http` and `https`. A report about path traversal in the manifest, a lock that can be stolen, or a download that follows an unexpected redirect is in scope.

Each desktop app has its own repository and its own advisory page. A bug that exists only in one app should be reported there.

## Model index

The remote model index is accepted only when `index.json.sig` verifies with a bundled Ed25519 public key and the document is at least as new as the bundled pin. A missing signature, a bad signature, or an older index is logged and the bundled snapshot is used. The private key is a CI secret in the index repository and is not stored here.
