# Q3261: verify_transaction_inclusion_v2 endian-sensitive txid interpretation double redemption across reorg

## Question
Can an unprivileged attacker call `verify_transaction_inclusion_v2` against a block whose canonical hash was cached offchain from `get_last_block_header` one transaction earlier using choose txid and sibling bytes that are sensitive to how `H256` values are serialized and interpreted between RPC hex and onchain Borsh inputs, so that verification lets the same economic event be proven once before and once after a short reorg so value can be claimed twice?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion_v2 + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public `verify_transaction_inclusion_v2`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `coinbase_tx_id`, `coinbase_merkle_proof`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: choose txid and sibling bytes that are sensitive to how `H256` values are serialized and interpreted between RPC hex and onchain Borsh inputs to force double redemption across reorg
- Invariant to test: proof verification must not depend on an endian mismatch between offchain proof construction and onchain `H256` handling
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion_v2` around this exact state transition and assert it never returns `true` in a way that enables double redemption across reorg.
