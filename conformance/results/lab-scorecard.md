# Database access conformance

Each row is a safety guarantee a scoped or read-only database access layer should uphold. A tick means the layer refused or neutralised the payload; a cross means the payload executed.

| Guarantee | Lokra | Naive proxy (keyword filter) |
|---|---|---|
| Reject more than one statement in a single call (CWE-89) | ✅ | ❌ |
| A read must not hide a write in a CTE (CWE-89) | ✅ | ❌ |
| SELECT must not create or write a table (CWE-89) | ✅ | ❌ |
| Must not run operating-system commands (COPY ... TO PROGRAM) (CWE-78) | ✅ | ❌ |
| Must not read files from the database host (CWE-73) | ✅ | ❌ |
| Must not switch to another database role (CWE-269) | ✅ | ❌ |
| A read must not take write locks (FOR UPDATE) (CWE-667) | ✅ | ❌ |
| An agent must not read another tenant's rows (CWE-639) | ✅ | ❌ |
| Sensitive identifiers must be masked in results (CWE-200) | ✅ | ❌ |
| Masking must survive a renamed column (CWE-200) | ✅ | ❌ |
| Identifiers inside free text must be masked (CWE-200) | ✅ | ❌ |

- **Lokra**: 11/11 upheld
- **Naive proxy (keyword filter)**: 0/11 upheld
