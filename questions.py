import json
import os

MAX_REPO = 40
SOURCE_REPO = 'near/nearcore'
REPO_NAME = 'nearcore'
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
    'chain/chain-primitives/src/error.rs',
    'chain/chain-primitives/src/lib.rs',
    'chain/chain/src/approval_verification.rs',
    'chain/chain/src/backfill_receipt_to_tx.rs',
    'chain/chain/src/block_processing_utils.rs',
    'chain/chain/src/chain.rs',
    'chain/chain/src/chain_update.rs',
    'chain/chain/src/doomslug.rs',
    'chain/chain/src/flat_storage_init.rs',
    'chain/chain/src/garbage_collection.rs',
    'chain/chain/src/genesis.rs',
    'chain/chain/src/lib.rs',
    'chain/chain/src/metrics.rs',
    'chain/chain/src/pending.rs',
    'chain/chain/src/receipt_to_tx.rs',
    'chain/chain/src/resharding/event_type.rs',
    'chain/chain/src/resharding/flat_storage_resharder.rs',
    'chain/chain/src/resharding/manager.rs',
    'chain/chain/src/resharding/migrations.rs',
    'chain/chain/src/resharding/mod.rs',
    'chain/chain/src/resharding/resharding_actor.rs',
    'chain/chain/src/resharding/trie_state_resharder.rs',
    'chain/chain/src/resharding/types.rs',
    'chain/chain/src/runtime/errors.rs',
    'chain/chain/src/runtime/metrics.rs',
    'chain/chain/src/runtime/mod.rs',
    'chain/chain/src/runtime/signer_overlay.rs',
    'chain/chain/src/runtime/trie_update_wrapper.rs',
    'chain/chain/src/sharding.rs',
    'chain/chain/src/signature_verification.rs',
    'chain/chain/src/spice/block_application.rs',
    'chain/chain/src/spice/chain.rs',
    'chain/chain/src/spice/chunk_application.rs',
    'chain/chain/src/spice/chunk_validation.rs',
    'chain/chain/src/spice/core.rs',
    'chain/chain/src/spice/core_writer_actor.rs',
    'chain/chain/src/spice/mod.rs',
    'chain/chain/src/state_snapshot_actor.rs',
    'chain/chain/src/store/mod.rs',
    'chain/chain/src/store/utils.rs',
    'chain/chain/src/store_validator.rs',
    'chain/chain/src/store_validator/validate.rs',
    'chain/chain/src/types.rs',
    'chain/chain/src/update_shard.rs',
    'chain/chain/src/validate.rs',
    'chain/chunks-primitives/src/error.rs',
    'chain/chunks-primitives/src/lib.rs',
    'chain/chunks/src/adapter.rs',
    'chain/chunks/src/chunk_cache.rs',
    'chain/chunks/src/client.rs',
    'chain/chunks/src/lib.rs',
    'chain/chunks/src/logic.rs',
    'chain/chunks/src/metrics.rs',
    'chain/chunks/src/shards_manager_actor.rs',
    'chain/client-primitives/src/debug.rs',
    'chain/client-primitives/src/lib.rs',
    'chain/client-primitives/src/types.rs',
    'chain/client/src/adapter.rs',
    'chain/client/src/chunk_endorsement_handler.rs',
    'chain/client/src/chunk_inclusion_tracker.rs',
    'chain/client/src/chunk_producer.rs',
    'chain/client/src/client.rs',
    'chain/client/src/client_actor.rs',
    'chain/client/src/lib.rs',
    'chain/client/src/metrics.rs',
    'chain/client/src/pending_transaction_queue.rs',
    'chain/client/src/prepare_transactions.rs',
    'chain/client/src/rpc_handler.rs',
    'chain/epoch-manager/src/adapter.rs',
    'chain/epoch-manager/src/epoch_info_aggregator.rs',
    'chain/epoch-manager/src/epoch_sync.rs',
    'chain/epoch-manager/src/genesis.rs',
    'chain/epoch-manager/src/lib.rs',
    'chain/epoch-manager/src/metrics.rs',
    'chain/epoch-manager/src/reward_calculator.rs',
    'chain/epoch-manager/src/shard_assignment/mod.rs',
    'chain/epoch-manager/src/shard_assignment/sticky_resharding.rs',
    'chain/epoch-manager/src/shard_tracker.rs',
    'chain/epoch-manager/src/validator_selection.rs',
    'chain/jsonrpc-primitives/src/errors.rs',
    'chain/jsonrpc-primitives/src/lib.rs',
    'chain/jsonrpc-primitives/src/types/call_function.rs',
    'chain/jsonrpc-primitives/src/types/mod.rs',
    'chain/jsonrpc-primitives/src/types/transactions.rs',
    'chain/jsonrpc/client/src/lib.rs',
    'chain/jsonrpc/src/api/call_function.rs',
    'chain/jsonrpc/src/api/transactions.rs',
    'chain/jsonrpc/src/lib.rs',
    'chain/pool/src/lib.rs',
    'chain/pool/src/metrics.rs',
    'chain/pool/src/types.rs',
    'core/async-derive/src/lib.rs',
    'core/async/src/functional.rs',
    'core/async/src/futures.rs',
    'core/async/src/instrumentation/data.rs',
    'core/async/src/instrumentation/instrumented_window.rs',
    'core/async/src/instrumentation/metrics.rs',
    'core/async/src/instrumentation/mod.rs',
    'core/async/src/instrumentation/queue.rs',
    'core/async/src/instrumentation/reader.rs',
    'core/async/src/instrumentation/writer.rs',
    'core/async/src/lib.rs',
    'core/async/src/messaging.rs',
    'core/async/src/multithread/mod.rs',
    'core/async/src/multithread/runtime_handle.rs',
    'core/async/src/multithread/sender.rs',
    'core/async/src/thread_pool.rs',
    'core/async/src/tokio/futures.rs',
    'core/async/src/tokio/mod.rs',
    'core/async/src/tokio/runtime.rs',
    'core/async/src/tokio/runtime_handle.rs',
    'core/async/src/tokio/sender.rs',
    'core/chain-configs/src/client_config.rs',
    'core/chain-configs/src/genesis_config.rs',
    'core/chain-configs/src/genesis_validate.rs',
    'core/chain-configs/src/lib.rs',
    'core/chain-configs/src/metrics.rs',
    'core/chain-configs/src/updatable_config.rs',
    'core/crypto/src/errors.rs',
    'core/crypto/src/hash.rs',
    'core/crypto/src/hash_domain.rs',
    'core/crypto/src/key_conversion.rs',
    'core/crypto/src/key_file.rs',
    'core/crypto/src/lib.rs',
    'core/crypto/src/signature.rs',
    'core/crypto/src/signer.rs',
    'core/crypto/src/traits.rs',
    'core/crypto/src/util.rs',
    'core/crypto/src/vrf.rs',
    'core/dyn-configs/src/lib.rs',
    'core/dyn-configs/src/metrics.rs',
    'core/external-storage/src/lib.rs',
    'core/o11y/src/env_filter.rs',
    'core/o11y/src/io_tracer.rs',
    'core/o11y/src/lib.rs',
    'core/o11y/src/log_config.rs',
    'core/o11y/src/log_counter.rs',
    'core/o11y/src/metrics.rs',
    'core/o11y/src/opentelemetry.rs',
    'core/o11y/src/reload.rs',
    'core/o11y/src/span_wrapped_msg.rs',
    'core/o11y/src/subscriber.rs',
    'core/parameters/src/config.rs',
    'core/parameters/src/config_store.rs',
    'core/parameters/src/cost.rs',
    'core/parameters/src/lib.rs',
    'core/parameters/src/parameter.rs',
    'core/parameters/src/parameter_table.rs',
    'core/parameters/src/view.rs',
    'core/parameters/src/vm.rs',
    'core/primitives-core/src/account.rs',
    'core/primitives-core/src/apply.rs',
    'core/primitives-core/src/chains.rs',
    'core/primitives-core/src/code.rs',
    'core/primitives-core/src/config.rs',
    'core/primitives-core/src/deterministic_account_id.rs',
    'core/primitives-core/src/errors.rs',
    'core/primitives-core/src/gas.rs',
    'core/primitives-core/src/global_contract.rs',
    'core/primitives-core/src/hash.rs',
    'core/primitives-core/src/lib.rs',
    'core/primitives-core/src/serialize.rs',
    'core/primitives-core/src/trie_key.rs',
    'core/primitives-core/src/types.rs',
    'core/primitives-core/src/universal_account_id.rs',
    'core/primitives-core/src/universal_state_init.rs',
    'core/primitives-core/src/version.rs',
    'core/primitives/src/action/delegate.rs',
    'core/primitives/src/action/mod.rs',
    'core/primitives/src/bandwidth_scheduler.rs',
    'core/primitives/src/block.rs',
    'core/primitives/src/block_body.rs',
    'core/primitives/src/block_header.rs',
    'core/primitives/src/challenge.rs',
    'core/primitives/src/chunk_apply_stats.rs',
    'core/primitives/src/congestion_info.rs',
    'core/primitives/src/epoch_block_info.rs',
    'core/primitives/src/epoch_info.rs',
    'core/primitives/src/epoch_manager.rs',
    'core/primitives/src/epoch_sync.rs',
    'core/primitives/src/errors.rs',
    'core/primitives/src/genesis/block.rs',
    'core/primitives/src/genesis/chunk.rs',
    'core/primitives/src/genesis/mod.rs',
    'core/primitives/src/lib.rs',
    'core/primitives/src/merkle.rs',
    'core/primitives/src/optimistic_block.rs',
    'core/primitives/src/profile_data_v2.rs',
    'core/primitives/src/profile_data_v3.rs',
    'core/primitives/src/rand.rs',
    'core/primitives/src/receipt.rs',
    'core/primitives/src/reed_solomon.rs',
    'core/primitives/src/sandbox.rs',
    'core/primitives/src/shard_layout/mod.rs',
    'core/primitives/src/shard_layout/utils.rs',
    'core/primitives/src/shard_layout/v0.rs',
    'core/primitives/src/shard_layout/v1.rs',
    'core/primitives/src/shard_layout/v2.rs',
    'core/primitives/src/shard_layout/v3.rs',
    'core/primitives/src/sharding.rs',
    'core/primitives/src/sharding/shard_chunk_header_inner.rs',
    'core/primitives/src/signable_message.rs',
    'core/primitives/src/spice/chunk_endorsement.rs',
    'core/primitives/src/spice/mod.rs',
    'core/primitives/src/spice/partial_data.rs',
    'core/primitives/src/spice/state_witness.rs',
    'core/primitives/src/state.rs',
    'core/primitives/src/state_record.rs',
    'core/primitives/src/telemetry.rs',
    'core/primitives/src/transaction.rs',
    'core/primitives/src/trie_key.rs',
    'core/primitives/src/trie_split.rs',
    'core/primitives/src/types.rs',
    'core/primitives/src/universal_state_init.rs',
    'core/primitives/src/upgrade_schedule.rs',
    'core/primitives/src/utils.rs',
    'core/primitives/src/utils/compression.rs',
    'core/primitives/src/utils/io.rs',
    'core/primitives/src/utils/min_heap.rs',
    'core/primitives/src/version.rs',
    'core/primitives/src/views.rs',
    'core/store/src/adapter/chain_store.rs',
    'core/store/src/adapter/chunk_store.rs',
    'core/store/src/adapter/epoch_store.rs',
    'core/store/src/adapter/flat_store.rs',
    'core/store/src/adapter/mod.rs',
    'core/store/src/adapter/trie_store.rs',
    'core/store/src/columns.rs',
    'core/store/src/config.rs',
    'core/store/src/contract.rs',
    'core/store/src/db/colddb.rs',
    'core/store/src/db/metadata.rs',
    'core/store/src/db/mixeddb.rs',
    'core/store/src/db/mod.rs',
    'core/store/src/db/recoverydb.rs',
    'core/store/src/db/refcount.rs',
    'core/store/src/db/rocksdb.rs',
    'core/store/src/db/rocksdb/instance_tracker.rs',
    'core/store/src/db/rocksdb/snapshot.rs',
    'core/store/src/db/slice.rs',
    'core/store/src/db/splitdb.rs',
    'core/store/src/deserialized_column.rs',
    'core/store/src/flat/chunk_view.rs',
    'core/store/src/flat/delta.rs',
    'core/store/src/flat/manager.rs',
    'core/store/src/flat/metrics.rs',
    'core/store/src/flat/mod.rs',
    'core/store/src/flat/storage.rs',
    'core/store/src/flat/types.rs',
    'core/store/src/genesis/initialization.rs',
    'core/store/src/genesis/mod.rs',
    'core/store/src/genesis/state_applier.rs',
    'core/store/src/lib.rs',
    'core/store/src/merkle_proof.rs',
    'core/store/src/metrics/mod.rs',
    'core/store/src/metrics/rocksdb_metrics.rs',
    'core/store/src/node_storage/mod.rs',
    'core/store/src/node_storage/opener.rs',
    'core/store/src/store.rs',
    'core/store/src/trie/config.rs',
    'core/store/src/trie/from_flat.rs',
    'core/store/src/trie/iterator.rs',
    'core/store/src/trie/mem/arena/alloc.rs',
    'core/store/src/trie/mem/arena/concurrent.rs',
    'core/store/src/trie/mem/arena/frozen.rs',
    'core/store/src/trie/mem/arena/hybrid.rs',
    'core/store/src/trie/mem/arena/metrics.rs',
    'core/store/src/trie/mem/arena/mod.rs',
    'core/store/src/trie/mem/arena/single_thread.rs',
    'core/store/src/trie/mem/construction.rs',
    'core/store/src/trie/mem/flexible_data/children.rs',
    'core/store/src/trie/mem/flexible_data/encoding.rs',
    'core/store/src/trie/mem/flexible_data/extension.rs',
    'core/store/src/trie/mem/flexible_data/mod.rs',
    'core/store/src/trie/mem/flexible_data/value.rs',
    'core/store/src/trie/mem/freelist.rs',
    'core/store/src/trie/mem/iter.rs',
    'core/store/src/trie/mem/loading.rs',
    'core/store/src/trie/mem/lookup.rs',
    'core/store/src/trie/mem/memtrie_update.rs',
    'core/store/src/trie/mem/memtries.rs',
    'core/store/src/trie/mem/metrics.rs',
    'core/store/src/trie/mem/mod.rs',
    'core/store/src/trie/mem/nibbles_utils.rs',
    'core/store/src/trie/mem/node/encoding.rs',
    'core/store/src/trie/mem/node/mod.rs',
    'core/store/src/trie/mem/node/view.rs',
    'core/store/src/trie/mem/parallel_loader.rs',
    'core/store/src/trie/mod.rs',
    'core/store/src/trie/nibble_slice.rs',
    'core/store/src/trie/ops/insert_delete.rs',
    'core/store/src/trie/ops/interface.rs',
    'core/store/src/trie/ops/iter.rs',
    'core/store/src/trie/ops/mod.rs',
    'core/store/src/trie/ops/resharding.rs',
    'core/store/src/trie/ops/squash.rs',
    'core/store/src/trie/outgoing_metadata.rs',
    'core/store/src/trie/prefetching_trie_storage.rs',
    'core/store/src/trie/raw_node.rs',
    'core/store/src/trie/receipts_column_helper.rs',
    'core/store/src/trie/shard_tries.rs',
    'core/store/src/trie/split.rs',
    'core/store/src/trie/state_snapshot.rs',
    'core/store/src/trie/trie_recording.rs',
    'core/store/src/trie/trie_storage.rs',
    'core/store/src/trie/trie_storage_update.rs',
    'core/store/src/trie/update.rs',
    'core/store/src/trie/update/iterator.rs',
    'core/store/src/utils/mod.rs',
    'core/time/src/clock.rs',
    'core/time/src/lib.rs',
    'core/time/src/serde.rs',
    'nearcore/src/append_only_map.rs',
    'nearcore/src/lib.rs',
    'nearcore/src/metrics.rs',
    'runtime/near-vm-runner/src/cache.rs',
    'runtime/near-vm-runner/src/errors.rs',
    'runtime/near-vm-runner/src/features.rs',
    'runtime/near-vm-runner/src/imports.rs',
    'runtime/near-vm-runner/src/lib.rs',
    'runtime/near-vm-runner/src/logic/alt_bn128.rs',
    'runtime/near-vm-runner/src/logic/bls12381.rs',
    'runtime/near-vm-runner/src/logic/context.rs',
    'runtime/near-vm-runner/src/logic/dependencies.rs',
    'runtime/near-vm-runner/src/logic/errors.rs',
    'runtime/near-vm-runner/src/logic/gas_counter.rs',
    'runtime/near-vm-runner/src/logic/logic.rs',
    'runtime/near-vm-runner/src/logic/mod.rs',
    'runtime/near-vm-runner/src/logic/recorded_storage_counter.rs',
    'runtime/near-vm-runner/src/logic/types.rs',
    'runtime/near-vm-runner/src/logic/utils.rs',
    'runtime/near-vm-runner/src/logic/vmstate.rs',
    'runtime/near-vm-runner/src/metrics.rs',
    'runtime/near-vm-runner/src/prepare.rs',
    'runtime/near-vm-runner/src/prepare/instrument_v3.rs',
    'runtime/near-vm-runner/src/prepare/prepare_v2.rs',
    'runtime/near-vm-runner/src/prepare/prepare_v3.rs',
    'runtime/near-vm-runner/src/profile.rs',
    'runtime/near-vm-runner/src/runner.rs',
    'runtime/near-vm-runner/src/utils.rs',
    'runtime/near-vm-runner/src/wasmtime_runner/logic.rs',
    'runtime/near-vm-runner/src/wasmtime_runner/mod.rs',
    'runtime/near-vm-runner/src/wasmtime_runner/trap_classification.rs',
    'runtime/near-wallet-contract/implementation/address-registrar/src/lib.rs',
    'runtime/near-wallet-contract/implementation/wallet-contract/src/error.rs',
    'runtime/near-wallet-contract/implementation/wallet-contract/src/eth_emulation.rs',
    'runtime/near-wallet-contract/implementation/wallet-contract/src/ethabi_utils.rs',
    'runtime/near-wallet-contract/implementation/wallet-contract/src/internal.rs',
    'runtime/near-wallet-contract/implementation/wallet-contract/src/lib.rs',
    'runtime/near-wallet-contract/implementation/wallet-contract/src/near_action.rs',
    'runtime/near-wallet-contract/implementation/wallet-contract/src/types.rs',
    'runtime/runtime/src/access_keys.rs',
    'runtime/runtime/src/action_validation.rs',
    'runtime/runtime/src/actions.rs',
    'runtime/runtime/src/adapter.rs',
    'runtime/runtime/src/bandwidth_scheduler/distribute_remaining.rs',
    'runtime/runtime/src/bandwidth_scheduler/mod.rs',
    'runtime/runtime/src/bandwidth_scheduler/scheduler.rs',
    'runtime/runtime/src/bandwidth_scheduler/simulator.rs',
    'runtime/runtime/src/cache_warming.rs',
    'runtime/runtime/src/config.rs',
    'runtime/runtime/src/congestion_control.rs',
    'runtime/runtime/src/contract_code.rs',
    'runtime/runtime/src/conversions.rs',
    'runtime/runtime/src/deterministic_account_id.rs',
    'runtime/runtime/src/ext.rs',
    'runtime/runtime/src/function_call.rs',
    'runtime/runtime/src/global_contracts.rs',
    'runtime/runtime/src/lib.rs',
    'runtime/runtime/src/metrics.rs',
    'runtime/runtime/src/pipelining.rs',
    'runtime/runtime/src/prefetch.rs',
    'runtime/runtime/src/receipt_manager.rs',
    'runtime/runtime/src/state_viewer/errors.rs',
    'runtime/runtime/src/state_viewer/mod.rs',
    'runtime/runtime/src/types.rs',
    'runtime/runtime/src/verifier.rs',
    'utils/config/src/lib.rs',
    'utils/near-cache/src/cell.rs',
    'utils/near-cache/src/lib.rs',
    'utils/near-cache/src/sync.rs',
    'utils/near-stable-hasher/src/lib.rs',
    'utils/stdx/src/lib.rs',
]

target_scopes = [
    'Critical. An unprivileged attacker can cause stealing or loss of funds by making nearcore accept or apply an invalid transaction, receipt, state transition, contract execution result, or shard state update.',
    'Critical. An unprivileged attacker can trigger an unauthorized transaction, balance manipulation, fee payment bypass, or transaction manipulation through broken access-key, nonce, signature, delegate-action, or receipt validation.',
    'Critical. An unprivileged attacker can break contract execution flows or cross-shard receipt semantics so honest nodes execute the wrong action, wrong receiver, wrong gas or deposit accounting, or wrong refund path.',
    'Critical. An unprivileged attacker can trigger a consensus flaw through ordinary user-controlled transactions, contracts, or default RPC submission paths alone, with no trusted or network-level role.',
    'High. An unprivileged attacker can corrupt trie, flat-storage, resharding, canonical state, or receipt routing through ordinary execution paths so valid balances, contract state, or receipts are lost, duplicated, or applied in the wrong place.',
    'High. An unprivileged attacker can trigger a non-network-level denial of service through transactions, contract execution, or default RPC submission, materially stalling execution or block processing without requiring a hardfork to fix.',
]

NEARCORE_ALLOWED_IMPACT_SCOPE = '## nearcore Allowed Impact Gate\nMatch the live HackenProof scope for https://github.com/near/nearcore only, but constrain questions to an unprivileged user attacker model:\n- stealing or loss of funds.\n- unauthorized transaction.\n- transaction manipulation.\n- price manipulation when caused by nearcore execution, state, or consensus bugs reachable from ordinary user actions.\n- fee payment bypass.\n- balance manipulation.\n- contract execution flow breakage.\n- consensus flaws reachable from ordinary user actions alone.\n- cryptographic flaws reachable from user-submitted transactions, contracts, or proofs handled on normal execution paths.\n- denial of service only when it is not network-level and could be fixed without a hardfork.\nOut of scope: network-level DoS, peer- or validator-controlled attacker assumptions, attacker-controlled sync providers, admin or trusted-role abuse, key compromise, social engineering, spam, non-nearcore code, misconfiguration without a code bug, disabled or non-default-only features, tests, mocks, fixtures, scripts, docs-only issues, local tooling, manifest/build/generated files, fee-only nuisance issues, style, and dependency-only behavior.'

NEARCORE_AUDIT_PIVOTS = '## Smart Audit Pivots\n- Transaction and runtime path: access keys, nonce handling, delegate actions, gas and deposit accounting, receipt creation, and contract execution must preserve fund, authorization, and receipt invariants.\n- Execution-driven consensus path: blocks, chunks, approvals, epoch transitions, and shard routing should only be questioned where ordinary attacker-submitted transactions, receipts, or contract state can cause honest nodes to diverge or finalize the wrong state.\n- Storage and resharding path: trie, flat storage, canonical state transitions, resharding, and receipt routing must not lose, replay, cross-apply, or corrupt balances or contract state.\n- Public API path: standard JSON-RPC transaction submission and transaction-related request parsing must not let an unprivileged user reach an in-scope transaction, execution, consensus, cryptographic, or non-network-level DoS impact.'


def question_generator(target_file: str) -> str:
    """
    Generate security questions for one nearcore target.
    """

    prompt = f"""
    Generate nearcore security questions for this exact target file:

    {target_file}

    Project lens:
    nearcore is the NEAR L1 reference client. Focus on unprivileged entry via transactions, delegate actions, contract inputs, staking actions, and default-enabled JSON-RPC methods that accept user-submitted execution data.

    Impact gate:
    {NEARCORE_ALLOWED_IMPACT_SCOPE}

    {NEARCORE_AUDIT_PIVOTS}

    Rules:
    * Treat `File Name:` as the exact file and `Scope:` as the only impact.
    * Assume repo context is accessible; do not ask for code.
    * The attacker is strictly unprivileged and must act through their own accounts, transactions, contracts, or standard JSON-RPC requests tied to those user actions.
    * Do not rely on validator, block-producer, peer, sync-provider, admin, signer, RPC operator, database, or infrastructure control unless scoped code lets an unprivileged attacker reach the same effect through ordinary user actions.
    * Trusted key compromise, malicious deployment, off-repo infrastructure failures, disabled features, and non-nearcore code are out of scope unless scoped code fails to authenticate, bind, or validate them.
    * Exclude network-level DoS, tests, mocks, fixtures, scripts, docs-only issues, local tooling, manifest/build/generated files, fee-only nuisance issues, style, and dependency-only behavior.
    * Generate 18 to 26 high-signal questions with non-overlapping root causes.
    * Name the exact corrupted value: account balance, staking balance, access-key permission, nonce, gas or deposit accounting, receipt destination, receipt refund path, state root, chunk header, epoch info, trie node, flat-storage entry, cryptographic binding, or transaction authorization result.
    * Every question must be testable with a Rust unit, integration, property, or fuzz-style test.

    Each question must include target symbol, attacker-controlled input, required state, call path, broken invariant, corrupted value, scoped impact, and proof idea.

    Output only valid Python. No markdown. No explanations.

    questions = [
    "[File: {target_file}] [Symbol: symbol_or_module] Can attacker-controlled INPUT under REQUIRED_STATE reach CALL_PATH and violate INVARIANT, corrupting EXACT_VALUE_AT_RISK with scoped impact SCOPE_IMPACT? Proof idea: write a Rust test that drives ENTRYPOINT through the vulnerable state transition and asserts EXPECTED_SAFETY_PROPERTY.",
    ]
    """
    return prompt


def audit_format(question: str) -> str:
    """
    Generate a focused nearcore exploit-question validation prompt.
    """
    return f"""# NEARCORE QUESTION REVIEW

## Exploit Question
{question}

## Scope Rules
- Audit only nearcore production code in this repository.
- Ignore tests, mocks, fixtures, scripts, generated artifacts, local tooling, and docs-only issues with no code-level impact.
- Do not ask for repo contents or claim files are missing.

## Objective
    Decide whether the question leads to a real nearcore vulnerability. The attacker must be unprivileged and must enter through transaction, contract, staking, receipt, storage, or standard RPC submission flows available in scoped code.

    Reject claims needing privileged key compromise, malicious deployment, off-repo infrastructure control, honest external behavior without a scoped validation failure, or network-level DoS. Reject any claim that needs the attacker to already be validator, block producer, peer, admin, or signer. Prefer #NoVulnerability unless the path proves material fund loss, unauthorized transaction or manipulation, contract execution breakage, consensus failure, cryptographic failure, or non-network-level DoS.

## Required Impacts
{NEARCORE_ALLOWED_IMPACT_SCOPE}

{NEARCORE_AUDIT_PIVOTS}

## Method
1. Trace the unprivileged entrypoint.
2. Map it to exact scoped files and functions.
3. Follow the full path through validation, runtime logic, storage mutation, and final fund, state, or consensus effects.
4. Identify the exact corrupted value and who loses funds, authority, or liveness.
5. Reject if existing guards preserve the invariant or if impact is immaterial.

## Reject Immediately
- Privileged key compromise, validator or admin config mistakes, or malicious deployment assumptions without a scoped code bypass.
- Honest external chain, storage, OS, or service behavior unless scoped validation or binding is missing.
- View-only mismatches, harmless deserialization differences, fee-only issues, logs, style, dependency-only behavior, tests, mocks, fixtures, scripts, or docs-only issues.

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
    Generate a cross-project analog scan prompt for nearcore issues.
    """
    prompt = f"""# ANALOG SCAN PROMPT

## External Report
{report}

## Task
Use the external report only as a bug-class seed. Search nearcore transaction, runtime, consensus, sharding, storage, resharding, and RPC-adjacent execution code for a native analog with concrete repository impact.

## Required Impacts
{NEARCORE_ALLOWED_IMPACT_SCOPE}

{NEARCORE_AUDIT_PIVOTS}

Report only if this repository has its own reachable root cause, unprivileged trigger, broken invariant, exact corrupted value, and matching target scope or allowed impact. Reject privileged assumptions, malicious deployment, external-system-only issues, network-level DoS, dependency-only behavior, and anything outside the production surface.

## Work Plan
1. Classify the external bug into one nearcore invariant.
2. Map it to exact scoped files/functions.
3. Trace attacker input through production validation and state updates.
4. Identify the wrong balance, nonce, receipt, state root, epoch assignment, trie value, refund path, or authorization decision.
5. Reject if existing guards preserve the invariant or the impact is not material.

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
    Generate a strict nearcore validation prompt for security claims.
    """
    prompt = f"""# VALIDATION PROMPT

## Security Claim
{report}

## Rules
- Validate only the submitted claim against nearcore production code in this repository.
- Do not invent a stronger claim, change target scope, or upgrade severity without evidence.
- A valid issue must be triggered by an unprivileged external attacker using only capabilities exposed by scoped code.
- Trusted key compromise, malicious deployment, and off-repo infra control are out unless the code fails to authenticate, bind, or validate them.
- Reject any claim that needs the attacker to already hold validator, block-producer, peer, admin, database, signer, or infrastructure privileges.
- Reject tests, mocks, fixtures, scripts, local tooling, docs-only issues, manifest/build/generated-file issues, network-level DoS, fee-only issues, crashes without an in-scope impact, style, and dependency-only bugs.
- The final impact must match one `target_scopes` item or allowed impact below and identify the exact corrupted value.

## Required Impacts
{NEARCORE_ALLOWED_IMPACT_SCOPE}

{NEARCORE_AUDIT_PIVOTS}

## Required Checks
1. Exact file/function references in scoped code.
2. Clear broken nearcore invariant tied to funds, transaction authorization, contract execution flow, consensus safety, cryptographic binding, or canonical state correctness.
3. Reachable exploit path: preconditions -> attacker input -> production call path -> bad value.
4. Existing guards reviewed and shown insufficient.
5. Exact wrong value named: account balance, staking balance, access-key permission, nonce, receipt target, refund target, gas or deposit amount, state root, chunk header, epoch info, trie value, flat-storage entry, or auth decision.
6. Reproducible proof path: Rust unit, integration, property, or fuzz-style test.

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
