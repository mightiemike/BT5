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
    "crates/apollo_config/src/behavior_mode.rs",
    "crates/apollo_config/src/command.rs",
    "crates/apollo_config/src/converters.rs",
    "crates/apollo_config/src/loading.rs",
    "crates/apollo_config/src/secrets.rs",
    "crates/apollo_config/src/validators.rs",
    "crates/apollo_config_manager/src/config_manager.rs",
    "crates/apollo_config_manager/src/config_manager_runner.rs",
    "crates/apollo_consensus/src/types.rs",
    "crates/apollo_consensus_orchestrator/src/orchestrator_versioned_constants.rs",
    "crates/apollo_gateway_config/src/compiler_version.rs",
    "crates/apollo_gateway_config/src/config.rs",
    "crates/apollo_http_server_config/src/config.rs",
    "crates/apollo_l1_provider_config/src/config.rs",
    "crates/apollo_mempool_config/src/config.rs",
    "crates/apollo_mempool_p2p_config/src/config.rs",
    "crates/apollo_network/src/authentication/negotiator.rs",
    "crates/apollo_network/src/discovery/identify_impl.rs",
    "crates/apollo_network/src/gossipsub_impl.rs",
    "crates/apollo_network/src/mixed_behaviour.rs",
    "crates/apollo_network/src/sqmr/messages.rs",
    "crates/apollo_network/src/sqmr/protocol.rs",
    "crates/apollo_network_types/src/network_types.rs",
    "crates/apollo_node_config/src/component_config.rs",
    "crates/apollo_node_config/src/component_execution_config.rs",
    "crates/apollo_node_config/src/config_utils.rs",
    "crates/apollo_node_config/src/definitions.rs",
    "crates/apollo_node_config/src/node_config.rs",
    "crates/apollo_node_config/src/version.rs",
    "crates/apollo_p2p_sync_config/src/config.rs",
    "crates/apollo_protobuf/src/codec.rs",
    "crates/apollo_protobuf/src/consensus.rs",
    "crates/apollo_protobuf/src/converters/class.rs",
    "crates/apollo_protobuf/src/converters/common.rs",
    "crates/apollo_protobuf/src/converters/consensus.rs",
    "crates/apollo_protobuf/src/converters/event.rs",
    "crates/apollo_protobuf/src/converters/header.rs",
    "crates/apollo_protobuf/src/converters/receipt.rs",
    "crates/apollo_protobuf/src/converters/rpc_transaction.rs",
    "crates/apollo_protobuf/src/converters/state_diff.rs",
    "crates/apollo_protobuf/src/converters/transaction.rs",
    "crates/apollo_protobuf/src/mempool.rs",
    "crates/apollo_protobuf/src/protobuf.rs",
    "crates/apollo_protobuf/src/protobuf/protoc_output.rs",
    "crates/apollo_protobuf/src/sync.rs",
    "crates/apollo_protobuf/src/transaction.rs",
    "crates/apollo_rpc/src/version_config.rs",
    "crates/apollo_rpc/src/v0_8/block.rs",
    "crates/apollo_rpc/src/v0_8/broadcasted_transaction.rs",
    "crates/apollo_rpc/src/v0_8/deprecated_contract_class.rs",
    "crates/apollo_rpc/src/v0_8/state.rs",
    "crates/apollo_rpc/src/v0_8/transaction.rs",
    "crates/apollo_rpc_execution/src/objects.rs",
    "crates/apollo_sierra_compilation_config/src/config.rs",
    "crates/apollo_signature_manager/src/blake_utils.rs",
    "crates/apollo_signature_manager/src/signature_manager.rs",
    "crates/apollo_starknet_client/src/reader/objects/block.rs",
    "crates/apollo_starknet_client/src/reader/objects/pending_data.rs",
    "crates/apollo_starknet_client/src/reader/objects/state.rs",
    "crates/apollo_starknet_client/src/reader/objects/transaction.rs",
    "crates/apollo_starknet_client/src/writer/objects/response.rs",
    "crates/apollo_starknet_client/src/writer/objects/transaction.rs",
    "crates/apollo_storage/src/db/serialization.rs",
    "crates/apollo_storage/src/deprecated/migrations.rs",
    "crates/apollo_storage/src/deprecated/serializers.rs",
    "crates/apollo_storage/src/serialization/mod.rs",
    "crates/apollo_storage/src/serialization/serializers.rs",
    "crates/apollo_storage/src/version.rs",
    "crates/apollo_transaction_converter/src/transaction_converter.rs",
    "crates/blockifier/src/blockifier_versioned_constants.rs",
    "crates/blockifier/src/context.rs",
    "crates/blockifier/src/state/compiled_class_hash_migration.rs",
    "crates/blockifier/src/transaction/objects.rs",
    "crates/native_blockifier/src/py_objects.rs",
    "crates/papyrus_common/src/deprecated_class_abi.rs",
    "crates/papyrus_common/src/python_json.rs",
    "crates/shared_execution_objects/src/central_objects.rs",
    "crates/starknet_api/src/abi.rs",
    "crates/starknet_api/src/block.rs",
    "crates/starknet_api/src/block_hash.rs",
    "crates/starknet_api/src/contract_class.rs",
    "crates/starknet_api/src/contract_class/compiled_class_hash.rs",
    "crates/starknet_api/src/contract_class/structs.rs",
    "crates/starknet_api/src/core.rs",
    "crates/starknet_api/src/crypto.rs",
    "crates/starknet_api/src/data_availability.rs",
    "crates/starknet_api/src/deprecated_contract_class.rs",
    "crates/starknet_api/src/executable_transaction.rs",
    "crates/starknet_api/src/execution_resources.rs",
    "crates/starknet_api/src/hash.rs",
    "crates/starknet_api/src/rpc_transaction.rs",
    "crates/starknet_api/src/serde_utils.rs",
    "crates/starknet_api/src/staking.rs",
    "crates/starknet_api/src/state.rs",
    "crates/starknet_api/src/transaction.rs",
    "crates/starknet_api/src/transaction/fields.rs",
    "crates/starknet_api/src/transaction_hash.rs",
    "crates/starknet_api/src/type_utils.rs",
    "crates/starknet_api/src/versioned_constants_logic.rs",
    "crates/starknet_committer/src/db/serde_db_utils.rs",
    "crates/starknet_committer/src/hash_function/hash.rs",
    "crates/starknet_committer/src/patricia_merkle_tree/leaf/leaf_serde.rs",
    "crates/starknet_os/src/io/os_input.rs",
    "crates/starknet_os/src/io/os_output.rs",
    "crates/starknet_os/src/io/os_output_types.rs",
    "crates/starknet_os/src/hints/enum_definition.rs",
    "crates/starknet_os/src/hints/enum_generation.rs",
    "crates/starknet_os/src/hints/types.rs",
]

target_scopes = [
    "Critical. Unprivileged-user-triggered protobuf, JSON, serde, bincode, or storage serialization ambiguity makes two production components interpret the same block, transaction, class, receipt, event, state diff, or consensus message differently.",
    "Critical. Unprivileged-user-triggered transaction hash, class hash, compiled class hash, block hash, signature preimage, Blake/Pedersen/Poseidon domain, or commitment encoding bug validates the wrong object.",
    "Critical. Unprivileged-user-triggered versioned constants, protocol config, compiler version, chain id, RPC version, or feature-boundary bug applies rules for the wrong Starknet version during validation, execution, sync, or admission.",
    "Critical. Unprivileged-user-triggered storage migration, deprecated serializer, schema version, DB key, or historical object conversion changes the meaning of already-stored protocol data.",
    "High. Unprivileged-user-triggered network handshake, peer identity, SQMR/gossipsub message, or capability negotiation bug accepts or advertises a protocol mode inconsistent with actual validation behavior.",
    "High. Unprivileged-user-triggered RPC/client conversion, pending-data object, deprecated class ABI, or transaction converter path silently changes field meaning across public API and internal executable types.",
    "High. Unprivileged-user-triggered config loading/validation or component execution config path allows a production node to run with mutually inconsistent protocol, chain, compiler, storage, or component settings visible to public flows.",
]

EXECUTION_ALLOWED_IMPACT_SCOPE = """## Allowed Impact Scope
Only these impacts are valid:
- Critical. Invalid or unauthorized Starknet transaction accepted through account validation, signature, nonce, chain id, fee/resource bound, paymaster, or account-deployment logic.
- Critical. Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.
- Critical. Incorrect fee, gas, bouncer, resource accounting, refund, balance, or L1 gas price effect with economic impact.
- Critical. Wrong compiled class, CASM/native artifact, class hash, or contract code selected for execution.
- High. Mempool/gateway/RPC admission accepts invalid transactions or rejects valid transactions before sequencing.
- High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.
- High. Transaction conversion or signature/hash logic binds the wrong signer, hash, type, or executable payload."""

SMART_AUDIT_PIVOTS = """## Sequencer-Specific Audit Pivots
- Public-to-internal conversion: compare `RpcTransaction`, `InternalRpcTransactionWithoutTxHash`, `ConsensusTransaction`, `InternalConsensusTransaction`, and executable blockifier transactions. Bind chain id, tx type/version, resource bounds, DA modes, paymaster/account-deployment data, proof facts, class hash, compiled class hash, and deploy address.
- Hash/domain boundaries: `calculate_transaction_hash`, `ClassInfo`, `CompiledClassHash`, `ProofFacts::hash`, `PartialBlockHash`, `calculate_block_hash`, `concat_counts`, and `gas_prices_to_hash` must use canonical ordering, Starknet version gates, and domain constants with no cross-version collisions.
- Version/config boundaries: `VersionedConstants`, gateway compiler versions, node/component config, RPC version config, storage schema `Version`, and `BlockHashVersion` decide validation/execution/hash behavior. Look for one component accepting data under one version while another commits or serves it under another.
- Serialization/storage boundaries: protobuf converters, storage serializers, deprecated class ABI/transactions, client reader/writer objects, and native Python wrappers must preserve exact field meaning across public APIs, consensus messages, DB rows, and executable objects."""


def question_generator(target_file: str) -> str:
    """
    Generate compatibility and canonicalization questions for one target.
    """

    prompt = f"""
    Generate compatibility/canonicalization audit questions for this exact Starknet Sequencer target:

    {target_file}

    Lens:
    Focus on object identity and version boundaries. Look for ambiguous serialization, non-canonical hashing, signature-domain mistakes, protocol/config version drift, compiler-version selection, schema migration, deprecated object conversion, RPC/internal type mismatch, network capability negotiation, and storage key compatibility.

    Execution/admission impact gate:
    {EXECUTION_ALLOWED_IMPACT_SCOPE}

    {SMART_AUDIT_PIVOTS}

    Rules:
    * Treat `File Name:` as the exact file/module and `Scope:` as the only impact.
    * Assume repo context is accessible; do not ask for code.
    * Attacker is unprivileged: public RPC client, ordinary account/contract user, low-trust peer, or sender of public serialized protocol data.
    * Attacker may control serialized transactions/classes, RPC fields, network messages, calldata, contract class data, timing around supported version boundaries, and public L1/L2 data inputs.
    * Do not grant sequencer operator, validator/proposer, release manager, migration operator, node admin, database, config author, oracle, or trusted-service privileges unless the question proves an unprivileged bypass.
    * Malicious-peer-only behavior and invalid bytes are out of scope when they are rejected, ignored, disconnected, retried, rate-limited, or only waste resources.
    * Ordinary crash/DoS, unbounded CPU/memory/disk/cache/queue growth, OOM, leaks, performance-only degradation, tests, mocks, benches, generated data, scripts, deployments, and local tooling are out of scope unless one allowed impact above or the target Scope is concretely reached.
    * Generate 16 to 22 high-signal questions, mostly crossing serialization/hash/config/storage/RPC boundaries.
    * Name the exact divergent value: bytes, enum variant, field meaning, transaction hash, class hash, block hash, signature preimage, protocol version, chain id, compiler version, feature flag, DB key, schema version, capability, or internal executable object.
    * Every question must be testable with a Rust serialization/property/upgrade/migration test or focused local reproducer.

    Each question must include target symbol, attacker-controlled representation, version/config preconditions, call path, canonicalization invariant, exact divergent value, scoped impact, and proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_module] Can attacker-controlled REPRESENTATION under VERSION_OR_CONFIG_PRECONDITIONS pass CALL_PATH and violate CANONICALIZATION_OR_DOMAIN_INVARIANT, producing DIVERGENT_VALUE with scoped impact SCOPE_IMPACT? Proof idea: build a Rust serialization/upgrade/property reproducer over PARAMETERS and assert EXPECTED_CANONICAL_IDENTITY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a compatibility/canonicalization question validation prompt.
    """
    return f"""# COMPATIBILITY QUESTION REVIEW

## Question
{question}

## Boundary
Audit only production Sequencer files listed in `scope_files`. Ignore tests, mocks, fixtures, generated data, docs, benches, scripts, deployments, and local tools.

## Goal
Decide whether the question can expose a reachable serialization, hash-domain, signature-domain, config-version, migration, RPC conversion, storage schema, or network capability bug.

A valid issue must show an unprivileged public path where production code accepts, hashes, signs, verifies, stores, migrates, converts, or advertises an object with the wrong protocol meaning. Prefer #NoVulnerability unless the exact divergent value is concrete.

## Required Execution/Admission Impacts
{EXECUTION_ALLOWED_IMPACT_SCOPE}

{SMART_AUDIT_PIVOTS}

## Review Steps
1. Identify the target symbol and object/version it interprets.
2. Trace attacker-controlled representation through decoding, conversion, hashing, signing, config selection, migration, or negotiation.
3. Check canonical encodings, enum tags, optional fields, chain id, protocol/compiler versions, hash domains, DB keys, schema guards, and feature gates.
4. Reject if existing guards reject the representation or preserve meaning.

## Fast Rejections
- Requires operator/admin/validator/proposer/release-manager/migration/database/config/oracle privileges.
- Malicious-peer-only invalid bytes are rejected, ignored, disconnected, retried, rate-limited, or only waste resources.
- Ordinary crash, DoS, timeout, unbounded CPU/memory/disk/cache/queue growth, OOM, leaks, performance-only degradation, logging, style, dependency-only behavior.
- No exact divergent protocol-visible value or no unprivileged path.

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
    Generate an analog scan prompt for compatibility issues.
    """
    prompt = f"""# COMPATIBILITY ANALOG SCAN

## External Report
{report}

## Task
Use the external report only as a seed for a Sequencer-native analog in serialization, hashing, signature domains, versioned constants, compiler selection, chain id, RPC/internal conversion, storage schema, migration, config validation, or network capability negotiation.

## Required Execution/Admission Impacts
{EXECUTION_ALLOWED_IMPACT_SCOPE}

{SMART_AUDIT_PIVOTS}

Report only if this repo has its own reachable root cause, unprivileged trigger, broken compatibility/canonicalization invariant, exact divergent value, and matching target scope or one of the impacts above. Reject privileged operations, invalid bytes that are rejected, malicious-peer-only behavior, resource-only issues, unbounded growth, dependency-only behavior, and non-production files.

## Work Plan
1. Translate the external bug into one identity, domain, version, schema, config, or conversion invariant.
2. Map it to exact production symbols.
3. Trace attacker-controlled representation or version/config boundary through the call path.
4. Identify the wrong bytes, field meaning, hash, signature preimage, protocol version, compiler version, feature flag, DB key, schema output, capability, or executable object.
5. Reject if existing gates/canonicalization preserve meaning.

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
    Generate a strict compatibility/canonicalization validation prompt.
    """
    prompt = f"""# COMPATIBILITY VALIDATION

## Security Claim
{report}

## Validation Rules
- Validate only this claim against production Sequencer files in `scope_files`.
- A valid issue must be reachable through unprivileged serialized transactions/classes, RPC fields, network messages, public protocol data, or supported version/config boundaries exposed to public flows.
- Reject operator/admin/validator/proposer/release-manager/migration/database/config/oracle assumptions, invalid bytes that are rejected, malicious-peer-only behavior, crash/DoS, unbounded CPU/memory/disk/cache/queue growth, OOM, leaks, resource-only issues, tests/mocks/generated files, docs, scripts, deployments, dependency-only bugs, and downstream misuse.
- The final impact must match one allowed scope below or one execution/admission impact, and name the exact divergent value.

## Required Execution/Admission Impacts
{EXECUTION_ALLOWED_IMPACT_SCOPE}

{SMART_AUDIT_PIVOTS}

## Allowed Impact Scope
Only these impacts are valid:
- Critical. Production components interpret the same serialized block, transaction, class, receipt, event, state diff, or consensus message differently.
- Critical. Hash/signature/commitment encoding validates the wrong transaction, class, block, signature preimage, or committed object.
- Critical. Versioned constants, protocol config, chain id, compiler version, RPC version, or feature boundary applies wrong Starknet rules.
- Critical. Storage migration, deprecated serializer, schema version, DB key, or historical conversion changes stored protocol data meaning.
- High. Network handshake, peer identity, SQMR/gossipsub message, or capability negotiation lies about actual validation behavior.
- High. RPC/client conversion or transaction converter changes public field meaning when building internal executable types.
- High. Config loading/validation allows mutually inconsistent production protocol, chain, compiler, storage, or component settings visible to public flows.

## Required Checks
1. Exact file/function/line references.
2. Broken serialization, canonicalization, domain, version, schema, config, conversion, or capability invariant.
3. Exploit path: preconditions -> attacker representation -> call path -> divergent value.
4. Existing gates/canonicalization shown insufficient.
5. Reproducible Rust serialization/property/upgrade/migration test or focused local reproducer.

## Output
If valid, output exactly:

Audit Report

## Title
[Clear vulnerability statement] - ([File: file_path])

## Summary
[2-3 sentence summary]

## Finding Description
[Code path, root cause, exploit flow, and failed guards]

## Impact Explanation
[Concrete allowed impact and severity]

## Likelihood Explanation
[Attacker capability and conditions]

## Recommendation
[Specific fix]

## Proof of Concept
[Minimal reproducible steps or test plan]

If invalid, output exactly:
#NoVulnerability found for this question.

Output only one of the two outcomes above. No extra text.
"""
    return prompt
