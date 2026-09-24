---
title: Prompt injection in retrieved evidence
provenance: Project-authored reference material for the CourseLLM evaluation corpus. Not an external source.
author: CourseLLM engineering
reviewed: 2026-01-01
---

# Prompt injection in retrieved evidence

A retrieval-augmented system places text from documents into a model's prompt.
Some of those documents are uploaded by users, and some may come from the open
web. Their text is **data**, but a language model reads the prompt as a single
sequence and has no intrinsic way to tell an instruction written by the
application from an instruction written by an attacker inside a document.

**Prompt injection** is the class of attack in which content in the untrusted
region is crafted to make the model treat it as a command — for example, text
that says to ignore previous instructions, to reveal the system prompt, or to
invoke a tool.

## Layered defences

No single control is sufficient, so defences are layered.

**Delimitation.** Untrusted content is wrapped in explicit markers, for example
`<untrusted_evidence>` ... `</untrusted_evidence>`, and the system prompt states
that everything inside the region is evidence to reason about and never an
instruction. Delimitation gives the model a clear boundary, but it is only a
convention; it is not enforcement.

**Neutralising reserved markers.** An attacker who can write the closing marker
into a document can end the region early and place their text where instructions
live. Before a passage is inserted, occurrences of the reserved marker are
stripped so the region cannot be closed by document text.

**Instruction precedence.** The system prompt states the rules in order: answer
from the evidence and cite it; if the evidence is partial, say what is supported
and what is not; if the evidence does not cover the question, say so rather than
answering from memory. A refusal path gives the model a safe action that does
not require trusting the document.

**Least privilege.** Tools that cause consequential side effects — sending
email, writing files, making purchases — are not exposed to the model by
default. Injection can only redirect capabilities that exist, so the smallest
capability set is the strongest containment.

**Output validation.** A generated answer's citations are checked against the
passages actually offered, and an unknown citation is removed. This does not
stop injection, but it stops an injected answer from manufacturing support that
does not exist.

## What detection can and cannot do

Heuristic or model-based injection detectors can flag suspicious documents, and
a flagged document can be quarantined or treated as higher risk. Detection is
probabilistic: it will miss novel phrasings and can flag benign text. It belongs
in the layer stack as a signal, not as the boundary. The boundary is that
untrusted text is never given the privileges of an instruction.
