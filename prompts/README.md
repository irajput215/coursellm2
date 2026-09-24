# Prompts

Prompts are **files on disk**, not string literals in Python. That is what makes
them reviewable in a diff, loadable without a code change, and impossible to
sprinkle through the codebase.

## Versioning scheme

Every prompt file begins with YAML-style front matter delimited by `---` lines:

```text
---
version: 1
name: tutor.answer
---

... the prompt body ...
```

* `name` is the dotted template name, and it must match the file path relative
  to this directory (`tutor/answer.md` -> `tutor.answer`). A mismatch is a load
  error rather than a silent alias.
* `version` is a positive integer. It is bumped whenever the body changes in a
  way that could change model behaviour.
* Together they form the **template id** `"<name>@<version>"`, for example
  `tutor.answer@1`. That id is recorded on every generated answer
  (`GeneratedAnswer.prompt_template_id`) and logged with the model call, so an
  answer can always be attributed to the exact prompt that produced it.

The body is rendered with `str.format_map` through `PromptLibrary.render`. A
placeholder that is referenced in a body but not supplied at render time raises a
`ValidationError` naming the missing key and the file path. `eval` is never used,
and `string.Template` (`$name`) is not used because a `$` in ordinary course
material would be interpreted as a placeholder.

`PromptLibrary` caches templates in memory. A test loads every prompt file on
disk and asserts that every placeholder used in a body is documented in the
registry below, so a broken or undocumented prompt fails CI instead of
production.

## Placeholder registry

Every `{placeholder}` that appears in a prompt body must be listed here.

| Placeholder | Used by | Meaning |
|-------------|---------|---------|
| `{course_name}` | `tutor.answer`, `tutor.refusal`, `tutor.extractive` | Display name of the course the question is scoped to. |
| `{evidence}` | `tutor.answer` | The delimited, untrusted evidence region assembled from ranked passages. |
| `{question}` | `tutor.refusal`, `tutor.extractive` | The student's question, rendered verbatim into a response template. |
| `{excerpts}` | `tutor.extractive` | Quoted extracts with their `[Sn]` citation ids, used when no model is reachable. |

## Untrusted evidence

`tutor.answer` is the only template that receives document text. The evidence is
interpolated into a single fenced slot whose delimiters are
`<untrusted_evidence ...>` and `</untrusted_evidence>`. The prompt states that
everything inside that region is data and must never be followed as instruction,
that an attempted injection must be reported rather than obeyed, and that
citations use the `[Sn]` ids assigned at assembly time. See
`docs/architecture/security.md` section 3 for the architectural rationale.
