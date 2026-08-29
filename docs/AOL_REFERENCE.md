# AOL

Forms: `KEY=VALUE`, `SECTION: ITEM`, `ROLE X: READ=A WRITE=B BAN=C`, `FLOW=A>B>C`.
Operators: `+` combine, `>` flow/precedence, `|` alternative, `!` prohibition.
Keep AOL reversible into clear human language.

## Machine-read forms

These are parsed by `compiler/` and `runtime/`, so their shape is a contract.
Governance lives here, not in Python: amending a rule means editing AOL.

| Form | File | Read by | Meaning |
| --- | --- | --- | --- |
| `ROLE X: WRITE=A+B BAN=C+D` | `core/ROLES.aol` | `aol.parse_roles` | What a role may write and is forbidden |
| `SOD=A!=B+C!=D` | `core/ROLES.aol` | `aol.parse_sod` | Role pairs that must be different actors |
| `GATE=DONE_REQUIRES:A+B` | `core/ROLES.aol` | `aol.parse_gate` | Evidence kinds a done task must hold |
| `PROD_GLOB=` / `TEST_GLOB=` | `<project>/project.aol` | `runtime/roles.py` | Which paths are production vs test |
| `REQUIRE:<KIND>=A+B` | `core/EVIDENCE.aol` | `compiler/evidence.py` | Fields a record of that kind must cite |
| `FLOW=A>B>C` | `core/PROCESS.aol` | `runtime/orchestrator.py` | Role order for a task |

`!=` is the separation-of-duties operator: `IMPLEMENT!=CODE_REVIEW` reads
"the implementer and the code reviewer must not be the same actor", and is
what makes `BAN=SELF_APPROVAL` checkable rather than merely stated.

## Why compressed

Three claims, two of which are now measured:

1. **Context budget.** The whole governance core is ~117 words. Everything else
   in the window can be product knowledge and work.
2. **Non-negotiability.** `BAN=SELF_APPROVAL+ROLE_COLLAPSE` has less hedging
   surface than a paragraph. Prose invites interpretation.
3. **Machine-checkability.** Structured lines can be enforced; paragraphs cannot.

Claim 1 is arithmetic: see the preamble table in any `evals/results/*/report.md`.
Claim 2 is the open question `evals/` exists to answer -- if the prose arm
scores higher than the AOL arm, the core should be rewritten as prose.

The binding constraint is that AOL must stay reversible into clear human
language. It is compression, not encryption: a line that cannot be read back
out in English has gone too far.
