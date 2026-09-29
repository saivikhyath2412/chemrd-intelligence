# Hindsight memory in ChemR&D

## Purpose

Hindsight is an optional continuity layer for the Research Assistant. It helps answer follow-up questions about the current user's earlier research conversations. It is not a chemical database, a literature source, or scientific evidence, and the assistant must never cite a memory as a source or let it override a relevant retrieved source.

## Request and data flow

1. The assistant reads the signed-in user's privacy preferences. `use_hindsight_memory` controls the external Hindsight integration; `save_assistant_history` independently controls local conversation history. Both are user-scoped.
2. For a substantive question, and only when both the user preference and server configuration allow it, the backend creates or updates a Hindsight bank for that account and asks Hindsight to recall relevant memories. Casual conversation is answered without a Hindsight recall.
3. The backend separately finds up to four relevant prior turns in the ChemR&D database. It combines those with up to four Hindsight results, labels the combined context as private memory in the model prompt, and keeps it separate from the retrieved evidence that is eligible for citations.
4. After a substantive answer, ChemR&D stores the question and answer in local history when that preference is enabled. If Hindsight is enabled, it also starts a background task to retain the turn in that user's Hindsight bank. The assistant response does not wait for the retention request to finish.

Short greetings and social replies are saved to local history (when enabled) but are not recalled from or retained in Hindsight. With Hindsight disabled or unavailable, local history can still support follow-up questions. The local history selector is a small, user-scoped keyword match; Hindsight performs the external memory retrieval.

## Account isolation and privacy

- Each external bank name is built from `HINDSIGHT_BANK_PREFIX` and the authenticated ChemR&D user ID. The application does not intentionally share a bank between accounts.
- The Hindsight API key is read from the process environment or a user-managed `.env`; it is sent as a bearer authorization header and is not placed in frontend JavaScript or prompts.
- Before sending a question or answer to Hindsight, ChemR&D redacts common credential assignments, bearer strings, and common provider-token formats. The Hindsight bank's retain instructions also prohibit retaining credentials and unrelated personal details. This is best-effort filtering, not a guarantee that every secret can be detected; do not enter passwords, API keys, or unrelated sensitive personal information into assistant conversations.
- When memory is sent to an AI provider for synthesis, it is included as private continuity context along with the current question. The provider is whichever configured assistant provider handles that request. Review that provider's data-handling terms before using confidential or regulated research data.
- Hindsight memory never appears in the `citations` array. Citations are generated from the separate retrieved-evidence path.

## Controls and configuration

`HINDSIGHT_ENABLED` defaults to `false`. The integration also requires `HINDSIGHT_API_URL`; the API key may be empty for a self-hosted service that does not require authentication. The default bank prefix is `chemrd`, the recall budget is `low`, the recall context cap is 1,200 tokens, and the HTTP timeout is 4 seconds. See the configuration table in the root [README](../README.md).

In Settings, turn off **Use Hindsight memory** to stop external recall and retention for that account. Turn off **Save assistant history** to stop local history storage and local-history recall. These controls are independent: disabling local history does not, by itself, disable Hindsight, and disabling Hindsight does not delete local history.

## Availability and lifecycle limitations

Hindsight errors and timeouts are treated as optional-service failures: the assistant continues without external memories. A successful assistant response does not guarantee that the background retention request succeeded. The status shown in a response primarily reports configuration/recall state, not confirmation that a later background retain completed.

**Clearing question history only clears the ChemR&D database.** The current integration does not call a Hindsight bank-deletion endpoint when local history is cleared or a ChemR&D account is deleted. If remote erasure is required, remove the corresponding bank or memories through the Hindsight service's supported controls as well. This is an explicit lifecycle limitation, not an implication that local deletion erases remote copies.

## Implementation map

- `backend/app/hindsight_memory.py`: Hindsight REST adapter, per-user bank naming, recall/retain requests, timeout and failure handling.
- `backend/app/memory_privacy.py`: best-effort redaction of common credential patterns.
- `backend/app/main.py`: combines private memory with the assistant workflow and keeps retention off the response path.
- `backend/app/llm.py`: formats memory as a separate, non-citable prompt section.
- `backend/app/store.py`: local, user-scoped question history and keyword-based continuity lookup.
- `tests/test_api.py`: privacy, user-isolation, failure, and prompt-separation regression tests.
