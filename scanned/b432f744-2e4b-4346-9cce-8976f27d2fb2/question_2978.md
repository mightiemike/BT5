# Q2978: verify_transaction_inclusion endian-sensitive txid interpretation wrong-block withdrawal unlock

## Question
Can an unprivileged attacker call `verify_transaction_inclusion` against a block whose coinbase path shares repeated sibling hashes because of duplicated odd leaves using choose txid and sibling bytes that are sensitive to how `H256` values are serialized and interpreted between RPC hex and onchain Borsh inputs, so that verification treats a withdrawal or release as backed by the canonical chain when the supporting transaction path is stale or wrong?

## Target
- File/function: contract/src/lib.rs::verify_transaction_inclusion + merkle-tools/src/lib.rs::compute_root_from_merkle_proof
- Entrypoint: public deprecated `verify_transaction_inclusion`
- Attacker controls: caller-chosen `tx_id`, `tx_block_blockhash`, `tx_index`, `merkle_proof`, `confirmations`, and the timing of the call relative to relayer updates and public GC
- Exploit idea: choose txid and sibling bytes that are sensitive to how `H256` values are serialized and interpreted between RPC hex and onchain Borsh inputs to force wrong-block withdrawal unlock
- Invariant to test: proof verification must not depend on an endian mismatch between offchain proof construction and onchain `H256` handling
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Initialize the contract with realistic headers, then call `verify_transaction_inclusion` around this exact state transition and assert it never returns `true` in a way that enables wrong-block withdrawal unlock.
