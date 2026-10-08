# Security policy

## Reporting a vulnerability

Please email **security@lokra.dev** instead of opening a public issue.

Include what you found, how to reproduce it, and the version or commit you tested.
We aim to acknowledge reports within 3 business days and will keep you updated
until a fix is released. We're happy to credit you in the release notes.

## In scope

- Any way for an agent to read or change rows, columns or tables its policy does not allow
- Bypassing write approval
- Sensitive identifiers that should be masked but are not
- Editing or deleting audit ledger entries without `lokra verify-ledger` detecting it
- Forging or extending agent tokens

## Supported versions

Only the latest release receives security fixes.
