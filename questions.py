import json
import os

# todo: if scope_files is: 500 > 50, 300 > 30 , 100 > 10
MAX_REPO = 25
SOURCE_REPO = "starkware-libs/sequencer"
REPO_NAME = "sequencer"
run_number = os.environ.get("GITHUB_RUN_NUMBER") or os.environ.get(
    "CI_PIPELINE_IID", "0"
)


def get_cyclic_index(run_number, max_index=100):
    """Convert run number to a cyclic index between 1 and max_index."""
    return (int(run_number) - 1) % max_index + 1


def load_repository_urls():
    """Load repository URLs from repositories.json."""
    repo_file = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "repositories.json"
    )
    if not os.path.exists(repo_file):
        return []

    try:
        with open(repo_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    if not isinstance(data, list):
        return []

    return [url for url in data if isinstance(url, str) and url.strip()]


if run_number == "0":
    BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"
else:
    repository_urls = load_repository_urls()
    if repository_urls:
        run_index = get_cyclic_index(run_number, len(repository_urls))
        BASE_URL = repository_urls[run_index - 1]
    else:
        BASE_URL = f"https://deepwiki.com/{SOURCE_REPO}"


scope_files = [
    "chain/chain/src/approval_verification.rs",
    "chain/chain/src/block_processing_utils.rs",
    "chain/chain/src/chain.rs",
    "chain/chain/src/chain_update.rs",
    "chain/chain/src/doomslug.rs",
    "chain/chain/src/lightclient.rs",
    "chain/chain/src/missing_chunks.rs",
    "chain/chain/src/orphan.rs",
    "chain/chain/src/pending.rs",
    "chain/chain/src/resharding/flat_storage_resharder.rs",
    "chain/chain/src/resharding/manager.rs",
    "chain/chain/src/resharding/migrations.rs",
    "chain/chain/src/resharding/resharding_actor.rs",
    "chain/chain/src/resharding/trie_state_resharder.rs",
    "chain/chain/src/runtime/mod.rs",
    "chain/chain/src/runtime/signer_overlay.rs",
    "chain/chain/src/runtime/trie_update_wrapper.rs",
    "chain/chain/src/sharding.rs",
    "chain/chain/src/signature_verification.rs",
    "chain/chain/src/spice/block_application.rs",
    "chain/chain/src/spice/chain.rs",
    "chain/chain/src/spice/chunk_application.rs",
    "chain/chain/src/spice/chunk_validation.rs",
    "chain/chain/src/spice/core.rs",
    "chain/chain/src/state_sync/adapter.rs",
    "chain/chain/src/state_sync/mod.rs",
    "chain/chain/src/state_sync/state_request_tracker.rs",
    "chain/chain/src/state_sync/utils.rs",
    "chain/chain/src/stateless_validation/chunk_endorsement.rs",
    "chain/chain/src/stateless_validation/chunk_validation.rs",
    "chain/chain/src/stateless_validation/processing_tracker.rs",
    "chain/chain/src/stateless_validation/state_witness.rs",
    "chain/chain/src/types.rs",
    "chain/chain/src/update_shard.rs",
    "chain/chain/src/validate.rs",
    "chain/chunks/src/chunk_cache.rs",
    "chain/chunks/src/client.rs",
    "chain/chunks/src/logic.rs",
    "chain/chunks/src/shards_manager_actor.rs",
    "chain/client/src/chunk_endorsement_handler.rs",
    "chain/client/src/chunk_inclusion_tracker.rs",
    "chain/client/src/chunk_producer.rs",
    "chain/client/src/client.rs",
    "chain/client/src/client_actor.rs",
    "chain/client/src/pending_transaction_queue.rs",
    "chain/client/src/prepare_transactions.rs",
    "chain/client/src/rpc_handler.rs",
    "chain/client/src/state_request_actor.rs",
    "chain/client/src/stateless_validation/chunk_endorsement.rs",
    "chain/client/src/stateless_validation/chunk_validation_actor.rs",
    "chain/client/src/stateless_validation/chunk_validator/mod.rs",
    "chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs",
    "chain/client/src/stateless_validation/partial_witness/encoding.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_deploys_tracker.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_witness_actor.rs",
    "chain/client/src/stateless_validation/partial_witness/partial_witness_tracker.rs",
    "chain/client/src/stateless_validation/shadow_validate.rs",
    "chain/client/src/stateless_validation/state_witness_producer.rs",
    "chain/client/src/stateless_validation/state_witness_tracker.rs",
    "chain/client/src/stateless_validation/validate.rs",
    "chain/client/src/sync/block.rs",
    "chain/client/src/sync/epoch.rs",
    "chain/client/src/sync/external.rs",
    "chain/client/src/sync/handler.rs",
    "chain/client/src/sync/header.rs",
    "chain/client/src/sync/state/chain_requests.rs",
    "chain/client/src/sync/state/downloader.rs",
    "chain/client/src/sync/state/mod.rs",
    "chain/client/src/sync/state/network.rs",
    "chain/client/src/sync/state/shard.rs",
    "chain/client/src/sync/state/task_tracker.rs",
    "chain/client/src/sync/state/util.rs",
    "chain/client/src/view_client_actor.rs",
    "chain/epoch-manager/src/epoch_info_aggregator.rs",
    "chain/epoch-manager/src/epoch_sync.rs",
    "chain/epoch-manager/src/lib.rs",
    "chain/epoch-manager/src/reward_calculator.rs",
    "chain/epoch-manager/src/shard_assignment/mod.rs",
    "chain/epoch-manager/src/shard_assignment/sticky_resharding.rs",
    "chain/epoch-manager/src/shard_tracker.rs",
    "chain/epoch-manager/src/validator_selection.rs",
    "chain/epoch-manager/src/validator_stats.rs",
    "chain/jsonrpc/src/api/blocks.rs",
    "chain/jsonrpc/src/api/call_function.rs",
    "chain/jsonrpc/src/api/chunks.rs",
    "chain/jsonrpc/src/api/light_client.rs",
    "chain/jsonrpc/src/api/query.rs",
    "chain/jsonrpc/src/api/status.rs",
    "chain/jsonrpc/src/api/transactions.rs",
    "chain/jsonrpc/src/api/validator.rs",
    "chain/jsonrpc/src/api/view_access_key.rs",
    "chain/jsonrpc/src/api/view_account.rs",
    "chain/jsonrpc/src/api/view_code.rs",
    "chain/jsonrpc/src/api/view_state.rs",
    "chain/jsonrpc/src/sharded_rpc.rs",
    "chain/network/src/accounts_data/mod.rs",
    "chain/network/src/announce_accounts/mod.rs",
    "chain/network/src/client.rs",
    "chain/network/src/network_protocol/edge.rs",
    "chain/network/src/network_protocol/mod.rs",
    "chain/network/src/network_protocol/peer.rs",
    "chain/network/src/network_protocol/state_sync.rs",
    "chain/network/src/peer/peer_actor.rs",
    "chain/network/src/peer_manager/peer_manager_actor.rs",
    "chain/network/src/routing/edge.rs",
    "chain/network/src/routing/graph/mod.rs",
    "chain/network/src/shards_manager.rs",
    "chain/network/src/state_sync.rs",
    "chain/network/src/state_witness.rs",
    "chain/network/src/types.rs",
    "chain/pool/src/lib.rs",
    "chain/pool/src/types.rs",
    "core/crypto/src/hash.rs",
    "core/crypto/src/hash_domain.rs",
    "core/crypto/src/signature.rs",
    "core/crypto/src/signer.rs",
    "core/crypto/src/vrf.rs",
    "core/primitives-core/src/account.rs",
    "core/primitives-core/src/apply.rs",
    "core/primitives-core/src/gas.rs",
    "core/primitives-core/src/hash.rs",
    "core/primitives-core/src/serialize.rs",
    "core/primitives-core/src/trie_key.rs",
    "core/primitives-core/src/types.rs",
    "core/primitives/src/action/mod.rs",
    "core/primitives/src/block.rs",
    "core/primitives/src/block_body.rs",
    "core/primitives/src/block_header.rs",
    "core/primitives/src/challenge.rs",
    "core/primitives/src/congestion_info.rs",
    "core/primitives/src/epoch_block_info.rs",
    "core/primitives/src/epoch_info.rs",
    "core/primitives/src/epoch_manager.rs",
    "core/primitives/src/epoch_sync.rs",
    "core/primitives/src/merkle.rs",
    "core/primitives/src/optimistic_block.rs",
    "core/primitives/src/receipt.rs",
    "core/primitives/src/reed_solomon.rs",
    "core/primitives/src/shard_layout/mod.rs",
    "core/primitives/src/shard_layout/v1.rs",
    "core/primitives/src/shard_layout/v2.rs",
    "core/primitives/src/shard_layout/v3.rs",
    "core/primitives/src/sharding.rs",
    "core/primitives/src/sharding/shard_chunk_header_inner.rs",
    "core/primitives/src/spice/chunk_endorsement.rs",
    "core/primitives/src/spice/partial_data.rs",
    "core/primitives/src/spice/state_witness.rs",
    "core/primitives/src/state.rs",
    "core/primitives/src/state_part.rs",
    "core/primitives/src/state_record.rs",
    "core/primitives/src/state_sync.rs",
    "core/primitives/src/stateless_validation/chunk_endorsement.rs",
    "core/primitives/src/stateless_validation/chunk_endorsements_bitmap.rs",
    "core/primitives/src/stateless_validation/contract_distribution.rs",
    "core/primitives/src/stateless_validation/partial_witness.rs",
    "core/primitives/src/stateless_validation/state_witness.rs",
    "core/primitives/src/stateless_validation/stored_chunk_state_transition_data.rs",
    "core/primitives/src/stateless_validation/validator_assignment.rs",
    "core/primitives/src/transaction.rs",
    "core/primitives/src/trie_key.rs",
    "core/primitives/src/trie_split.rs",
    "core/primitives/src/types.rs",
    "core/primitives/src/upgrade_schedule.rs",
    "core/primitives/src/validator_mandates/compute_price.rs",
    "core/primitives/src/validator_signer.rs",
    "core/store/src/adapter/chain_store.rs",
    "core/store/src/adapter/chunk_store.rs",
    "core/store/src/adapter/epoch_store.rs",
    "core/store/src/adapter/flat_store.rs",
    "core/store/src/adapter/trie_store.rs",
    "core/store/src/flat/delta.rs",
    "core/store/src/flat/manager.rs",
    "core/store/src/flat/storage.rs",
    "core/store/src/flat/types.rs",
    "core/store/src/merkle_proof.rs",
    "core/store/src/trie/from_flat.rs",
    "core/store/src/trie/iterator.rs",
    "core/store/src/trie/mem/loading.rs",
    "core/store/src/trie/mem/memtries.rs",
    "core/store/src/trie/mem/memtrie_update.rs",
    "core/store/src/trie/ops/insert_delete.rs",
    "core/store/src/trie/ops/interface.rs",
    "core/store/src/trie/ops/iter.rs",
    "core/store/src/trie/ops/resharding.rs",
    "core/store/src/trie/ops/squash.rs",
    "core/store/src/trie/raw_node.rs",
    "core/store/src/trie/receipts_column_helper.rs",
    "core/store/src/trie/shard_tries.rs",
    "core/store/src/trie/split.rs",
    "core/store/src/trie/state_parts.rs",
    "core/store/src/trie/state_snapshot.rs",
    "core/store/src/trie/trie_recording.rs",
    "core/store/src/trie/trie_storage.rs",
    "core/store/src/trie/trie_storage_update.rs",
    "core/store/src/trie/update.rs",
    "nearcore/src/config_validate.rs",
    "nearcore/src/state_sync.rs",
    "neard/src/cli.rs",
    "neard/src/main.rs",
    "runtime/near-vm-runner/src/cache.rs",
    "runtime/near-vm-runner/src/features.rs",
    "runtime/near-vm-runner/src/imports.rs",
    "runtime/near-vm-runner/src/logic/alt_bn128.rs",
    "runtime/near-vm-runner/src/logic/bls12381.rs",
    "runtime/near-vm-runner/src/logic/context.rs",
    "runtime/near-vm-runner/src/logic/gas_counter.rs",
    "runtime/near-vm-runner/src/logic/logic.rs",
    "runtime/near-vm-runner/src/logic/recorded_storage_counter.rs",
    "runtime/near-vm-runner/src/logic/vmstate.rs",
    "runtime/near-vm-runner/src/prepare.rs",
    "runtime/near-vm-runner/src/prepare/instrument_v3.rs",
    "runtime/near-vm-runner/src/prepare/prepare_v2.rs",
    "runtime/near-vm-runner/src/prepare/prepare_v3.rs",
    "runtime/near-vm-runner/src/runner.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/logic.rs",
    "runtime/near-vm-runner/src/wasmtime_runner/mod.rs",
    "runtime/runtime/src/access_keys.rs",
    "runtime/runtime/src/action_validation.rs",
    "runtime/runtime/src/actions.rs",
    "runtime/runtime/src/adapter.rs",
    "runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs",
    "runtime/runtime/src/bandwidth_scheduler/scheduler.rs",
    "runtime/runtime/src/cache_warming.rs",
    "runtime/runtime/src/congestion_control.rs",
    "runtime/runtime/src/contract_code.rs",
    "runtime/runtime/src/conversions.rs",
    "runtime/runtime/src/deterministic_account_id.rs",
    "runtime/runtime/src/ext.rs",
    "runtime/runtime/src/function_call.rs",
    "runtime/runtime/src/global_contracts.rs",
    "runtime/runtime/src/pipelining.rs",
    "runtime/runtime/src/prefetch.rs",
    "runtime/runtime/src/receipt_manager.rs",
    "runtime/runtime/src/types.rs",
    "runtime/runtime/src/verifier.rs",
]

target_scopes = [
    "Critical. Unprivileged-user-triggered Versioned Borsh/JSON/protobuf conversion, enum variant, or legacy field handling interprets the same stored or network object differently across supported nearcore versions.",
    "Critical. Unprivileged-user-triggered Protocol feature activation, epoch boundary, runtime config selection, or protocol version negotiation applies new rules too early, too late, or inconsistently across chain, runtime, client, and network code.",
    "Critical. Unprivileged-user-triggered Hash domain, signable message, approval id, endorsement id, transaction hash, or Merkle path construction collides across message types or protocol versions and validates the wrong object.",
    "Critical. Unprivileged-user-triggered Store schema migration, DB column mapping, cold/split storage transition, or archival migration changes the meaning of historical consensus data used after upgrade.",
    "Critical. Unprivileged-user-triggered Block, chunk, receipt, transaction, state part, or epoch proof serialization compatibility bug causes a valid upgraded node and a valid non-upgraded/window node to compute different hashes or validation results.",
    "High. Unprivileged-user-triggered Shard layout, account-id boundary, trie-key format, state record, or migration compatibility bug maps historical state to a different shard/account namespace after a protocol transition.",
    "High. Unprivileged-user-triggered Crypto key parsing, signature scheme tagging, public key conversion, or validator signer serialization accepts a key/signature under the wrong scheme or domain.",
    "High. Unprivileged-user-triggered Stable hashing, ordered map/set serialization, configuration hashing, or deterministic ID construction depends on non-canonical ordering or representation in a protocol-visible value.",
    "High. Unprivileged-user-triggered RPC, client config, genesis config validation, or network handshake compatibility path advertises or accepts a protocol capability that does not match the node's actual validation behavior.",
]


def question_generator(target_file: str) -> str:
    """
    Generate protocol-compatibility and serialization audit questions for one nearcore target.

    target_file format:
    "'File Name: core/primitives/src/block_header.rs -> Scope: Critical. Unprivileged-user-triggered Block, chunk, receipt, transaction, state part, or epoch proof serialization compatibility bug causes a valid upgraded node and a valid non-upgraded/window node to compute different hashes or validation results.'"
    """

    prompt = f"""
    ```

    Generate protocol-compatibility audit and fuzzing questions for this exact nearcore target:

    {target_file}

    Lens:
    This `nearcore` pass is about version boundaries and object identity. Do not duplicate the consensus, ledger, or data-availability profiles. Focus on serialization compatibility, protocol feature activation, migration semantics, hash domains, signable-message identity, schema evolution, and capability negotiation.

    Relevant mechanisms:
    Borsh/serde conversions, versioned block/chunk/header/receipt/transaction/state objects, `ProtocolFeature`, `ProtocolVersion`, `EpochConfig`, `RuntimeConfig`, `ShardLayout`, `TrieKey`, `StateRecord`, `CryptoHash`, `hash_domain`, `SignableMessage`, approvals, endorsements, validator signer/public key types, DB columns, migrations, split/cold storage transitions, genesis/config validation, RPC view types, and network handshake/protocol version fields.

    Ask from these angles:
    * Identity: does the same logical object always hash/sign/verify under one unambiguous domain?
    * Compatibility: do old and new object versions preserve meaning across supported upgrade windows?
    * Activation: is a feature gated at the exact epoch/protocol boundary for every caller that interprets the object?
    * Migration: does stored historical data keep the same semantics after schema or storage transitions?
    * Canonical form: are maps, enum variants, optional fields, key tags, and JSON/Borsh/protobuf conversions deterministic and unambiguous?

    Rules:
    * Treat `File Name:` as the exact file/module and `Scope:` as the only impact.
    * Assume full repo context is accessible; do not ask for code.
    * Attacker must be an unprivileged user: ordinary account holder, contract deployer/caller, public RPC client, or unauthenticated/low-trust peer using public protocol representations.
    * Unprivileged attacker may control serialized transactions signed by their own keys, receipts or state touched by their contracts, RPC inputs, public network messages, handshake fields they can send, and timing of public interactions around supported protocol upgrade boundaries.
    * Do not grant validator, block producer, chunk producer, node admin, release manager, migration operator, genesis/config author, or trusted infrastructure privileges unless the bug lets an unprivileged user bypass that boundary.
    * A malicious peer sending invalid bytes is not enough; ask only where nearcore accepts, stores, hashes, signs, verifies, migrates, or advertises an object with the wrong protocol meaning.
    * Do not rely on admin/operator mistakes, hand-edited genesis/config/DB, debug/adversarial flags, privileged upgrade operators, compromised validators, dependency-only bugs, or downstream misuse.
    * Exclude ordinary crashes, DoS, resource growth, OOM, logs, tests, mocks, benches, tooling, and memory-management hygiene unless a protocol-visible identity or compatibility result changes.
    * Generate 16 to 24 high-signal questions, with at least two thirds crossing serialization/version/hash/migration boundaries.
    * Every question must be testable with `cargo test --package ... --features test_features`, a property/fuzz test, an upgrade/migration test, a differential serialization test, or a focused local reproducer.
    * Name the exact value that may diverge: Borsh bytes, JSON field meaning, enum variant, protocol version, feature flag state, hash, signature domain, DB column key, migration output, shard layout, trie key, public key scheme, or advertised capability.

    Each question must include target symbol, attacker-controlled representation, upgrade/version preconditions, call path, compatibility invariant, exact divergent value, scoped impact, and proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_module] Can attacker-controlled REPRESENTATION under VERSION_PRECONDITIONS pass CALL_PATH and violate COMPATIBILITY_OR_DOMAIN_INVARIANT, producing DIVERGENT_VALUE with scoped impact SCOPE_IMPACT? Proof idea: build a Rust serialization/upgrade/property test over PARAMETERS and assert EXPECTED_CANONICAL_IDENTITY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a nearcore protocol-compatibility question validation prompt.
    """
    return f"""# PROTOCOL COMPATIBILITY QUESTION REVIEW

## Question
{question}

## Boundary
Audit only production nearcore files listed in `scope_files`. Ignore tests, docs, mocks, benches, fuzz harnesses, generated data, automation, packaging, scripts, and local tools. Do not ask for repo contents.

## Goal
Decide whether the question can expose a reachable compatibility, canonicalization, feature-gating, migration, hash-domain, signature-domain, or capability-negotiation bug.

A valid issue must show an unprivileged-user-reachable production path where nearcore accepts, hashes, signs, verifies, stores, migrates, or advertises an object with the wrong protocol meaning. Prefer #NoVulnerability unless the exact divergent representation or identity value is concrete.

Do not assume validator, block producer, chunk producer, node admin, release manager, migration operator, genesis/config author, or trusted infrastructure privileges unless the question proves an unprivileged bypass of that boundary.

## Review Steps
1. Identify the target symbol and the object/version it interprets.
2. Trace attacker-controlled representation through serialization, conversion, hashing, signing, feature gating, migration, or negotiation.
3. Check canonical encoding, enum tags, optional fields, protocol version gates, domain separators, DB column keys, and migration guards.
4. Name the exact bytes, field, hash, signature domain, schema version, feature flag, trie key, or advertised capability that becomes wrong.
5. Reject if current code rejects the representation or preserves meaning across the supported upgrade window.
6. Require file/function references and a realistic differential or upgrade test.

## Fast Rejections
- Admin/operator error, manual config/genesis/DB edits, wrong key custody, debug/adversarial mode, or unsupported upgrade path.
- Requires validator, block producer, chunk producer, node admin, release manager, migration operator, genesis/config author, or trusted infrastructure privileges not obtainable by an unprivileged user.
- Malicious-peer-only invalid bytes that are rejected or only waste resources.
- Ordinary crash, DoS, timeout, resource growth, OOM, leak, logging, style, or memory-management cleanup.
- Dependency-only behavior or downstream misuse outside nearcore APIs.
- No exact divergent protocol-visible value, or no supported attacker-controlled representation.
- Claim belongs only to ledger accounting, data availability, or generic network liveness rather than this target scope.

## Output
If valid:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If invalid, output exactly:
#NoVulnerability found for this question.
"""


def scan_format(report: str) -> str:
    """
    Generate a cross-project analog scan prompt for nearcore compatibility issues.
    """
    prompt = f"""# PROTOCOL COMPATIBILITY ANALOG SCAN

## External Report
{report}

## Task
Use the external report only as a seed for a nearcore-native issue in production `scope_files`. Look for analogs in versioned serialization, protocol feature activation, schema migration, hash/signature domains, deterministic hashing, key/scheme tagging, shard-layout compatibility, config validation, RPC type compatibility, or network capability negotiation.

Do not claim missing files. Do not audit tests, docs, mocks, benches, fuzz harnesses, generated data, scripts, packaging, or local tools.

## Analog Standard
Report only if nearcore has its own reachable root cause, unprivileged-user-controlled representation or public upgrade-boundary interaction, broken compatibility/domain invariant, exact divergent value, and scoped High/Critical impact.

Reject analogs based on admin mistakes, unsupported manual upgrades, privileged validator/block-producer/migration-operator roles, malicious-peer invalid bytes that are rejected, resource-only behavior, memory cleanup, dependency-only behavior, or downstream misuse.

## Work Plan
1. Translate the external bug into a nearcore invariant: canonical encoding, versioned meaning, feature boundary, migration semantics, hash domain, signature domain, deterministic ordering, or capability truthfulness.
2. Map it to exact production symbols.
3. Trace attacker-controlled representation or upgrade timing through the call path.
4. Identify the exact divergent Borsh bytes, JSON field, enum variant, protocol version, hash, signature domain, DB key, migration output, shard layout, trie key, key scheme, or advertised capability.
5. Reject if existing gates/canonicalization preserve meaning or if impact is not one of this file's scopes.

## Output (Strict)
If valid analog exists, output:

### Title
[Clear vulnerability statement] - ([File: file_path])

### Summary
### Finding Description
### Impact Explanation
### Likelihood Explanation
### Recommendation
### Proof of Concept

If not, output exactly:
#NoVulnerability found for this question.

No extra text.
"""
    return prompt


def validation_format(report: str) -> str:
    """
    Generate a strict nearcore protocol-compatibility validation prompt.
    """
    prompt = f"""# PROTOCOL COMPATIBILITY VALIDATION

## Security Claim
{report}

## Validation Rules
- Validate only this claim against production nearcore files in `scope_files`.
- A valid issue must be reachable by an unprivileged user through supported serialization, hashing, signing, protocol-version boundary, RPC conversion, network negotiation, or stored-object interpretation paths exposed to public inputs.
- The final impact must match one allowed scope below and name the exact divergent protocol-visible value.
- Reject admin/operator mistakes, manual DB/config/genesis edits, unsupported upgrade paths, debug/adversarial modes, compromised supermajorities, dependency-only bugs, downstream misuse, and environment-specific setup.
- Reject claims requiring validator, block producer, chunk producer, node admin, release manager, migration operator, genesis/config author, or trusted infrastructure privileges unless the report proves an unprivileged user can bypass that boundary.
- Reject malicious-peer-only claims where invalid bytes or messages are rejected, ignored, disconnected, rate-limited, or only waste resources.
- Reject ordinary crash/DoS, unbounded CPU/memory/disk/cache growth, leaks, OOM, logging/display issues, and Rust memory-management hygiene unless a protocol-visible identity or compatibility value changes.

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Unprivileged-user-triggered Versioned Borsh/JSON/protobuf conversion, enum variant, or legacy field handling interprets the same stored or network object differently across supported nearcore versions.
- Critical. Unprivileged-user-triggered Protocol feature activation, epoch boundary, runtime config selection, or protocol version negotiation applies new rules too early, too late, or inconsistently across chain, runtime, client, and network code.
- Critical. Unprivileged-user-triggered Hash domain, signable message, approval id, endorsement id, transaction hash, or Merkle path construction collides across message types or protocol versions and validates the wrong object.
- Critical. Unprivileged-user-triggered Store schema migration, DB column mapping, cold/split storage transition, or archival migration changes the meaning of historical consensus data used after upgrade.
- Critical. Unprivileged-user-triggered Block, chunk, receipt, transaction, state part, or epoch proof serialization compatibility bug causes a valid upgraded node and a valid non-upgraded/window node to compute different hashes or validation results.
- High. Unprivileged-user-triggered Shard layout, account-id boundary, trie-key format, state record, or migration compatibility bug maps historical state to a different shard/account namespace after a protocol transition.
- High. Unprivileged-user-triggered Crypto key parsing, signature scheme tagging, public key conversion, or validator signer serialization accepts a key/signature under the wrong scheme or domain.
- High. Unprivileged-user-triggered Stable hashing, ordered map/set serialization, configuration hashing, or deterministic ID construction depends on non-canonical ordering or representation in a protocol-visible value.
- High. Unprivileged-user-triggered RPC, client config, genesis config validation, or network handshake compatibility path advertises or accepts a protocol capability that does not match the node's actual validation behavior.

If the submitted claim does not concretely prove one of the allowed impacts above, it is invalid.

## Required Validation Checks
All must pass:
1. Exact in-scope file, function, and line or code references.
2. Clear broken serialization, canonicalization, version-gating, migration, hash-domain, signature-domain, scheme-tagging, deterministic-ordering, or capability invariant.
3. Reachable exploit path: preconditions -> attacker-controlled representation or upgrade timing -> production call path -> divergent protocol-visible value.
4. Existing enum/version/feature/domain/schema/canonicalization guards reviewed and shown insufficient.
5. Exact divergent value identified: Borsh bytes, JSON field meaning, enum variant, protocol version, feature flag, hash, signature domain, DB column key, migration output, shard layout, trie key, public key scheme, or advertised capability.
6. Concrete impact matching one allowed scope, with realistic likelihood.
7. Reproducible proof path: Rust serialization/property test, upgrade test, migration test, compatibility differential, or focused local reproducer.
8. No rejection reason from privileged-role requirements, admin error, unsupported upgrade path, malicious-peer-only behavior, resource-only behavior, dependency-only behavior, or scope exclusions.

## Silent Triage Questions
Before output, internally answer:
- Which representation, version boundary, domain, or migration is ambiguous?
- Can an unprivileged user trigger this without validator, block producer, chunk producer, node admin, release manager, migration operator, genesis/config author, or trusted infrastructure privileges?
- Which supported attacker-controlled bytes/fields/timing trigger it?
- What exact protocol-visible value diverges?
- Do existing version, feature, domain, schema, and canonicalization checks already prevent it?
- Is this more than invalid peer bytes being rejected or resource exhaustion?
- What exact test proves different accepted meaning, hash, signature result, migration output, or advertised capability?

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary of the bug and impact]

## Finding Description
[Exact code path, root cause, exploit flow, and why existing checks fail]

## Impact Explanation
[Concrete allowed repository impact and severity rationale]

## Likelihood Explanation
[Attacker capability, required conditions, feasibility, repeatability]

## Recommendation
[Specific fix guidance]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
