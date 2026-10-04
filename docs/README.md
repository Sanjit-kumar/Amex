# Transaction Standardizer: Documentation Home

| | |
|---|---|
| **Status** | Working prototype, runs locally. Not production. |
| **Owner** | Sanjit Das / (add team owner) |
| **Audience** | Engineers and reviewers new to the project |
| **Last updated** | 2026-10-04 |

## Pages

| # | Page | What you will learn |
|---|---|---|
| 1 | [Problem and goals](01-problem-and-goals.md) | How the process works today, what we are replacing, how we measure success |
| 2 | [Architecture](02-architecture.md) | Components, tech stack, prototype vs. production mapping |
| 3 | [AI pipeline](03-pipeline.md) | The three AI levels, the fast path, sequence of one upload |
| 4 | [Data model and validation](04-data-model.md) | The standard schema, versioning, status lifecycles, validation rules |
| 5 | [Review UI](05-review-ui.md) | The two browser pages and how a reviewer works |
| 6 | [Configuration and security](06-config-security.md) | Per-user AI credentials, card-number handling, SSO |
| 7 | [Roadmap and open questions](07-roadmap-and-open-questions.md) | Phases, risks, decisions we still need |
| 8 | [Run it locally](08-run-locally.md) | Setup, tests, API reference |

## The idea in one paragraph

Merchants send us transaction records in whatever format they have. Today a team of about 50 people converts each one
by hand into the Amex standard CSV and checks every transaction before sales uses it. This project replaces the
manual conversion with AI agents and keeps one human step: a reviewer compares the original file with the
standardized result, fixes anything wrong, and approves it. Only approved data reaches the sales team.

## Glossary

| Term | Meaning |
|---|---|
| **Standard format** | The fixed set of 13 transaction fields every record is converted to ([schema](04-data-model.md#standard-schema)) |
| **Level 1 / 2 / 3** | The three AI stages: vision, text, convert ([pipeline](03-pipeline.md)) |
| **Fast path** | Structured files with recognizable headers are mapped by code, with no AI call |
| **Record** | One standardized transaction. A file contains one or many |
| **Version** | Every edit creates a new version. Version 1 is always the AI's original output |
| **Curated store** | The output of approved records only. This is what sales consumes |
| **Mock mode** | Demo mode used when no AI credentials are configured. It must be off in real use |

> [!TIP]
> Diagrams are written in [Mermaid](https://mermaid.js.org/) and render automatically on GitHub. In VS Code, install
> the "Markdown Preview Mermaid Support" extension to see them locally.
