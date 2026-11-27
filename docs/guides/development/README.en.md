# Core development

Start with the ownership map, then follow a complete command/fact path through the application transaction. Source changes retain existing authority rather than adding a parallel execution or resource map.

[Module map](modules/domain-ports.en.md) · [Coding standards](coding-standards.md) · [Ownership model](control-ownership-model.md) · [V2 architecture](../../architecture/v2/README.en.md)

Use the repository-pinned Rust/Python toolchains. Run the narrow checks affected by a change; cross-contract, schema, checkpoint or delivery changes require the corresponding integration/restore gates. Documentation-only changes run the documentation suite and any affected packaged runbook checks.
