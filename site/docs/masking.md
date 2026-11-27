---
title: Masking
description: How Lokra masks sensitive values in query results, every masking strategy and detector, and which queries it refuses so a masked column cannot leave under another name.
---
Lokra masks sensitive values in every query result before it reaches the agent. You set it up once, in the `masking` block of `policies.yaml`, and it applies to every agent. This page explains the two kinds of masking, lists every strategy and detector, and shows which queries Lokra refuses to keep masked columns masked.

```yaml
masking:
  columns:
    email: redact
    phone: last3
    card_number: last4
  detectors: [card, email, phone, au_phone]
```

## How masking works

Masking has two parts that work together:

- **Column rules** (`masking.columns`) mask a result column by its name. Any column called `email`, in any table, is masked with the strategy you give it.
- **Detectors** (`masking.detectors`) scan text for values that look like a card number, an email address, a phone number and so on, wherever they appear: in a free-text note, inside JSON, in an array.

Lokra applies both to the rows it returns from the agent's `query` tool. Each result says what it masked:

```json
{
  "columns": ["id", "name", "email", "phone"],
  "rows": [[1, "Ana Lima", "[REDACTED]", "********678"]],
  "row_count": 1,
  "truncated": false,
  "masked": {"column:redact": 1, "column:last3": 1}
}
```

`column:<strategy>` counts values masked by a column rule; a detector name such as `card` counts values a detector replaced. The same counts go into the [audit ledger](/docs/audit) for every query. SQL text that Lokra stores in the ledger and shows in the dashboard, Slack and Lokra Cloud is scrubbed by the detectors too.

### Column rules

- Column names match case-insensitively. `Email`, `EMAIL` and `email` are the same rule.
- A rule applies to every result column with that name, whatever table it came from. If `phone` means a customer's mobile in one table and your public support line in another, both are masked.
- Inside JSON, object keys count as column names, at any depth. A `jsonb` column holding `{"contact": {"email": "ana@acme.example"}}` comes back as `{"contact": {"email": "[REDACTED]"}}`.
- `NULL` stays `NULL`.
- A value masked by a column rule is not scanned by detectors as well.

Because rules work on names, Lokra also checks every query so that a masked column can only reach the result under its own name. See [queries Lokra refuses](#queries-lokra-refuses).

### How detectors scan

- Detectors scan every text value that no column rule has masked: plain text columns, JSON keys and values, and each element of an array.
- They only scan text. A card number stored in a `bigint` column is not text, so only a column rule can mask it.
- They run in a fixed order, most specific first, whatever order you list them in. An IHI is checked before a card number, so it is never reported as `[CARD]`.
- Cards, IBANs, Medicare numbers and IHIs must pass their checksum, so ordinary numbers such as order IDs are left alone.

### Free text, JSON and arrays

With the policy above, a support note, a JSON column and a text array come back like this:

| Column | Stored value | Returned to the agent |
|---|---|---|
| `body` (text) | `Called back on 0412 345 678, card 4111 1111 1111 1111 declined` | `Called back on [PHONE], card [CARD] declined` |
| `metadata` (jsonb) | `{"contact": {"email": "ana@acme.example"}}` | `{"contact": {"email": "[REDACTED]"}}` |
| `tags` (text[]) | `["vip", "reach me at ana@acme.example"]` | `["vip", "reach me at [EMAIL]"]` |

In `metadata`, the `email` key matched the column rule. In `body` and `tags`, the detectors found the values.

## Strategies

A column rule maps a column name to one of these strategies:

| Strategy | What it returns | Example |
|---|---|---|
| `redact` | `[REDACTED]` | `ana@acme.example` → `[REDACTED]` |
| `last4` | The last 4 digits, the rest as `*` | `4111 1111 1111 1111` → `************1111` |
| `last3` | The last 3 digits | `+61 412 345 678` → `********678` |
| `last2` | The last 2 digits | `+61 412 345 678` → `*********78` |
| `year_only` | The year | `1990-04-02` → `1990` |
| `hash` | `h:` and the first 12 hex characters of the value's SHA-256 | `+61 412 345 678` → `h:912f919bbb0f` |
| `none` | The value unchanged | |

Details worth knowing:

- **`lastN`** works for any whole number of 1 or more, such as `last6`. When the value contains digits, only digits are kept and counted, so spaces, dashes and letters are dropped. A value with no digits keeps its last N characters: `Ana Lima` with `last3` becomes `*****ima`.
- **`year_only`** returns the year of a `date` or `timestamp` column. For a text column it returns the first four characters, which is the year only for ISO dates such as `1990-04-02`. Text such as `02/04/1990` would come back as `02/0`.
- **`hash`** gives the same output for the same value every time and for every agent, so an agent can count or match values without seeing them. It is not salted: anyone who can guess a value, such as a phone number or a birth date, can hash the guess and compare.
- **`none`** removes a column rule for that name. Detectors still scan the value, and the query check below does not apply to it.

A strategy name Lokra does not recognise is an error when the policy loads, and so is `last0`, which would show the whole value.

## Detectors

| Detector | What it matches | Validation | Replaced with |
|---|---|---|---|
| `ihi` | Australian Individual Healthcare Identifiers: 16 digits starting `800360`, spaces or dashes allowed | Luhn checksum | `[IHI]` |
| `medicare` | Australian Medicare card numbers: 10 digits starting with 2 to 6, optionally followed by the 1-digit reference number, spaces or dashes allowed | Medicare check digit | `[MEDICARE]` |
| `card` | Payment card numbers: 13 to 19 digits, spaces or dashes allowed, starting with a Visa, Mastercard, American Express, Diners, JCB, Discover or UnionPay prefix | Luhn checksum | `[CARD]` |
| `iban` | International bank account numbers in capitals, with or without spaces, for example `GB82 WEST 1234 5698 7654 32` | ISO 7064 mod-97 check | `[IBAN]` |
| `us_ssn` | US Social Security numbers written `078-05-1120` or `078 05 1120`, skipping numbers that cannot be issued (area `000`, `666` or `9xx`, group `00`, serial `0000`) | Format only | `[SSN]` |
| `email` | Email addresses | Format only | `[EMAIL]` |
| `au_phone` | Australian mobiles (`0412 345 678`, `+61 412 345 678`) and landlines with an `02`, `03`, `07` or `08` area code (`02 9876 5432`, `+61 2 9876 5432`) | Format only | `[PHONE]` |
| `phone` | International numbers with a `+` and country code (`+1 415 555 0132`, `+44 20 7946 0958`), and North American numbers such as `(415) 555-0132` or `415-555-0132` | 8 to 15 digits | `[PHONE]` |

The `card` detector checks the prefix as well as the checksum: Visa `4`, Mastercard `51`–`55` and `2221`–`2720`, American Express `34` and `37`, Diners `30`, `36` and `38`, JCB `35`, Discover `6011`, `644`–`649` and `65`, UnionPay `62`. A 16-digit number that fails the checksum, such as `4111 1111 1111 1112`, is left alone.

### What detectors leave alone

Detectors look for the usual written forms. They do not match:

- dates, times, IP addresses, version numbers and amounts such as `1,250,000.00`,
- order numbers and references such as `123456789` or `1234-5678`,
- an SSN or a North American phone number written without separators, such as `078051120` or `4155550132`,
- an Australian landline with the area code in brackets, such as `(02) 9876 5432`,
- an IBAN in lower case.

If a column holds one of these forms, give it a column rule.

> **Note:** Unknown detector names are an error. A policy listing `detectors: [phone_au]` makes every request fail with `unknown detectors: ['phone_au']`.

## Queries Lokra refuses

Column rules work on result column names. If an agent could return a masked column under another name, for example `SELECT email AS contact`, the rule would not match and the value would leave unmasked. So before a query runs, Lokra checks where each masked column ends up, and refuses the query if one could reach the result under any other name.

A masked column is any name in `masking.columns` whose strategy is not `none`. Lokra matches by name, not by table, so the check covers every column with that name.

### Inside a function, alias, cast, UNION or subquery

These are refused:

```sql
SELECT lower(email) FROM customers;
SELECT split_part(email, '@', 2) FROM customers;
SELECT email AS contact FROM customers;
SELECT email::text FROM customers;
SELECT max(email) FROM customers;
SELECT array_agg(email) FROM customers;
SELECT json_build_object('e', email) FROM customers;
SELECT row_number() OVER (ORDER BY email) FROM customers;
SELECT name FROM accounts UNION SELECT email FROM customers;
SELECT (SELECT email FROM customers LIMIT 1) AS x;
SELECT e FROM (SELECT email FROM customers) AS t(e);
```

Each one fails with:

```text
the masked column email can only be selected as itself, or used in WHERE, JOIN, GROUP BY or ORDER BY; it cannot go inside a function, alias, UNION or subquery result
```

### Whole-row references

A reference to a whole row carries every column in it, masked ones included:

```sql
SELECT row_to_json(c) FROM customers c;
SELECT c FROM customers c;
SELECT j.value FROM customers c, LATERAL jsonb_each_text(to_jsonb(c)) j;
```

```text
c refers to a whole row, which would carry masked columns; select the columns you need
```

A star inside an expression is refused the same way:

```sql
SELECT to_jsonb(c.*) FROM customers c;
```

```text
c.* inside an expression would carry masked columns; select the columns you need
```

Because masked columns are matched by name, this applies to every table once your policy has any masked column, even a table with nothing sensitive in it, such as `row_to_json(p)` on `products`.

### Functions that run a query from text

`query_to_xml`, `table_to_xml`, `cursor_to_xml`, `schema_to_xml`, `database_to_xml` and their `xmlschema` variants run a query that is passed in as a string, where Lokra's check cannot see it. They are refused for every agent, masking or not:

```text
the function query_to_xml() is not allowed
```

## Work with masking

Most queries need no change. These are all allowed:

```sql
SELECT id, name, email FROM customers;
SELECT email AS email FROM customers;
SELECT * FROM customers;
SELECT c.email, o.total FROM customers c JOIN orders o ON o.customer_id = c.id;
SELECT x.email FROM (SELECT email FROM customers) x;
WITH recent AS (SELECT email FROM customers) SELECT email FROM recent;
```

The column comes out under its own name, and Lokra masks it on the way out.

**Filter, join, group and sort on masked columns freely.** A masked column can appear anywhere that only decides which rows come back, including inside functions there:

```sql
SELECT id, name FROM customers WHERE lower(email) = 'ana@acme.example';
SELECT count(*), email FROM customers GROUP BY email;
SELECT id FROM customers ORDER BY lower(email);
```

> **Note:** Filtering lets an agent confirm a guess, for example `WHERE email = 'ana@acme.example'` returns a row only if that address exists. Masking stops the agent reading a value; it does not stop it testing one. If an agent must learn nothing about a column, leave the column out of its `read` grant.

**Need part of a value?** An agent cannot compute it in SQL, because the masked column would leave inside a function. Store the safe part under its own name instead, for example a generated column, and grant that:

```sql
ALTER TABLE customers ADD COLUMN email_domain text
  GENERATED ALWAYS AS (split_part(email, '@', 2)) STORED;
```

Then add `email_domain` to the agent's `read` list and run `lokra provision`. An agent that reads the table as `"*"` can see it straight away. Detectors still scan it.

## Check your masking

- `lokra check` warns about any column an agent can read that looks sensitive by name and has no column rule or matching detector, and reports rules that match no column. See [connect your database](/docs/connect-database#check-the-policy-with-lokra-check).
- `lokra try <agent> "<sql>"` shows exactly what the agent would get back, masking counts included.
- `lokra report` and `lokra evidence` summarise what each agent's queries masked. See [audit and reports](/docs/audit).
