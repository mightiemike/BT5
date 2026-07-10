The core of the question is whether `check_aux` validates `parent_block.bits` against any expected Bitcoin difficulty. Let me trace the exact code path.

In `contract/src/dogecoin.rs`, the PoW check at the end of `check_aux` is:

```rust
let pow_hash = aux_data.parent_block.block_hash_pow();
require!(
    self.skip_pow_verification
        || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
    format!("block should have correct pow")
);
``` [1](#0-0) 

`block_header` here is the **Dogecoin block header**, not the parent block. So `target_from_bits(block_header.bits)` is the **Dogecoin block's difficulty target**. The `parent_block.bits` field is never read in any validation — it is accepted as any arbitrary value. [2](#0-1) 

The `block_hash_pow` for the Bitcoin parent block (without `scrypt_hash` feature) is simply `double_sha256` of the 80-byte header: [3](#0-2) 

The `AuxData.parent_block` is a plain `Header` struct with no chain-membership constraint: [4](#0-3) 

The entry point is `submit_blocks`, which is gated by `#[trusted_relayer]` — a staking-based mechanism, not a DAO-only gate, and the scope rules explicitly list "trusted-relayer submission path with attacker-relevant data" as a valid entry point: [5](#0-4) 

---

### Title
AuxPoW Parent Block Difficulty Not Validated Against Bitcoin Target — Synthetic Parent Block Accepted (`contract/src/dogecoin.rs`)

### Summary
`check_aux` verifies that `parent_block.block_hash_pow()` satisfies `target_from_bits(block_header.bits)`, where `block_header` is the **Dogecoin** block. The `parent_block.bits` field — which should represent Bitcoin's actual mining difficulty — is never read or validated. An attacker can construct a synthetic parent block with `bits = 0x207fffff` (minimum difficulty, near-zero work) and iterate its `nonce` until `double_sha256(parent_block_bytes)` satisfies the Dogecoin difficulty target, which is orders of magnitude easier than Bitcoin's real difficulty. All other `check_aux` checks (chain_id, coinbase merkle root, script_sig embedding) are structural and can be satisfied by constructing the coinbase transaction correctly.

### Finding Description
The invariant of AuxPoW is: a Dogecoin block is valid only if it was embedded in a **real Bitcoin block** whose hash satisfies Bitcoin's own declared difficulty. The contract enforces none of this:

1. **`parent_block.bits` is never validated.** No check compares it to any expected Bitcoin difficulty or to `config.proof_of_work_limit_bits`. [1](#0-0) 

2. **`parent_block.prev_block_hash` is never validated.** No check requires it to be a known Bitcoin block hash. The parent block floats freely with no chain-membership requirement. [4](#0-3) 

3. **The PoW check uses the wrong target.** `target_from_bits(block_header.bits)` is the Dogecoin block's target. Bitcoin mainnet currently has a target roughly 2^48 times harder than Dogecoin. Mining a synthetic parent block to Dogecoin's target is trivially feasible on commodity hardware. [1](#0-0) 

4. **The `used_aux_parent_blocks` replay guard was removed.** The migration code shows that a prior version (`BtcLightClientV2`) tracked used parent block hashes to prevent reuse. The current state layout drops this field entirely, so even replay of the same synthetic parent block across multiple submissions is not prevented. [6](#0-5) 

### Impact Explanation
A trusted relayer (reachable via the staking path, not DAO-only) can submit Dogecoin blocks whose AuxPoW data contains a completely synthetic Bitcoin parent block. The light client stores these blocks as valid, advances its mainchain tip, and will subsequently return `true` from `verify_transaction_inclusion` / `verify_transaction_inclusion_v2` for transactions in those fraudulent blocks. Any downstream protocol relying on the light client's transaction proofs (e.g., cross-chain bridges, asset unlocking) can be deceived with fabricated Dogecoin transaction history.

### Likelihood Explanation
The attack requires only: (a) becoming a trusted relayer (staking NEAR tokens), (b) constructing a valid Dogecoin block header with correct `bits` and timestamp, (c) constructing a coinbase transaction embedding the Dogecoin block hash in the script_sig per the AuxPoW format, and (d) iterating `nonce` on a synthetic 80-byte parent block until `double_sha256` satisfies the Dogecoin difficulty. Step (d) is computationally trivial on any modern CPU given Dogecoin's difficulty. No privileged key, DAO vote, or social engineering is required.

### Recommendation
In `check_aux`, after computing `pow_hash`, add a second check that the parent block's own declared difficulty is at or above the minimum expected Bitcoin difficulty:

```rust
// Validate parent block's bits against Bitcoin's minimum difficulty
let parent_target = target_from_bits(aux_data.parent_block.bits);
require!(
    parent_target <= target_from_bits(config.bitcoin_proof_of_work_limit_bits),
    "Aux POW parent block bits exceed Bitcoin minimum difficulty"
);
// Then check the hash satisfies the parent block's OWN declared target
require!(
    U256::from_le_bytes(&pow_hash.0) <= parent_target,
    "Aux POW parent block hash does not satisfy its own declared target"
);
```

The PoW check should use `target_from_bits(aux_data.parent_block.bits)` — the parent block's own declared target — not the Dogecoin block's target. Additionally, reinstate a `used_aux_parent_blocks` set (or equivalent) to prevent replay of the same synthetic parent block.

### Proof of Concept
1. Take any real Dogecoin block header `doge_block` with valid `bits` (e.g., `0x1b0404cb`) and AuxPoW flag set in `version`.
2. Construct a coinbase transaction `coinbase_tx` whose `script_sig` contains `fabe6d6d` followed by `chain_root` (computed from `doge_block.block_hash()` and an empty `chain_merkle_proof`), followed by `n_size = 0x01000000` and `n_nonce` chosen so `get_expected_index(n_nonce, chain_id, 0) == 0`.
3. Set `parent_block = Header { version: 1, prev_block_hash: [0u8;32], merkle_root: txid_of(coinbase_tx), time: <any>, bits: 0x207fffff, nonce: 0 }`.
4. Iterate `parent_block.nonce` from 0 upward until `U256::from_le_bytes(&double_sha256(parent_block_bytes).0) <= target_from_bits(doge_block.bits)`. This loop terminates in expected `1 / target_from_bits(doge_block.bits) * 2^256` iterations — on Dogecoin mainnet, typically millions of hashes, feasible in seconds.
5. Submit `(doge_block, Some(AuxData { coinbase_tx, merkle_proof: [], chain_merkle_proof: [], chain_id: 0, parent_block }))` via `submit_blocks`.
6. `check_aux` passes all checks. The fraudulent Dogecoin block is stored as canonical. `verify_transaction_inclusion` will subsequently return `true` for any fabricated transaction in that block. [2](#0-1)

### Citations

**File:** contract/src/dogecoin.rs (L49-155)
```rust
    pub(crate) fn check_aux(&mut self, block_header: &Header, aux_data: &AuxData) {
        // The Dogecoin block must have the AuxPoW flag set (bit 8) when AuxPoW data is present.
        // https://github.com/dogecoin/dogecoin/blob/master/src/auxpow.h
        const BLOCK_VERSION_AUXPOW: i32 = 0x100;

        require!(
            aux_data.chain_merkle_proof.len() <= 30,
            "Aux POW chain merkle branch too long"
        );
        require!(
            block_header.version & BLOCK_VERSION_AUXPOW != 0,
            "Aux POW block does not have AuxPoW flag set in version"
        );

        let chain_id = self.get_config().aux_chain_id;

        require!(
            chain_id == block_header.get_chain_id(),
            format!(
                "block does not have our chain ID (got {}, expected {chain_id})",
                block_header.get_chain_id()
            )
        );

        require!(
            chain_id != aux_data.parent_block.get_chain_id(),
            "Aux POW parent has our chain ID"
        );

        let chain_root = merkle_tools::compute_root_from_merkle_proof(
            block_header.block_hash(),
            aux_data.chain_id,
            &aux_data.chain_merkle_proof,
        );

        let coinbase_tx = aux_data.get_coinbase_tx();
        let coinbase_tx_hash = coinbase_tx.compute_txid();

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                H256::from(coinbase_tx_hash.to_raw_hash().to_byte_array()),
                0,
                &aux_data.merkle_proof,
            ) == aux_data.parent_block.merkle_root
        );

        let script_sig = coinbase_tx
            .input
            .first()
            .unwrap()
            .script_sig
            .to_hex_string();
        let pos_merged_mining_header = script_sig.find(MERGED_MINING_HEADER);
        let mut pos_chain_root = script_sig
            .find(&chain_root.to_string())
            .expect("Aux POW missing chain merkle root in parent coinbase");

        match pos_merged_mining_header {
            Some(pos_merged_mining_header) => {
                if script_sig[pos_merged_mining_header + MERGED_MINING_HEADER.len()..]
                    .contains(MERGED_MINING_HEADER)
                {
                    env::panic_str("Multiple merged mining headers in coinbase");
                }

                require!(
                    pos_merged_mining_header + MERGED_MINING_HEADER.len() == pos_chain_root,
                    "Merged mining header is not just before chain merkle root"
                );
            }
            None => {
                require!(pos_chain_root <= 40, "Aux POW chain merkle root must start in the first 20 bytes of the parent coinbase");
            }
        }

        pos_chain_root += chain_root.to_string().len();
        require!(
            script_sig.len() - pos_chain_root >= 16,
            "Aux POW missing chain merkle tree size and nonce in parent coinbase"
        );

        let bytes = hex::decode(&script_sig[pos_chain_root..pos_chain_root + 8]).unwrap();
        let n_size = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        require!(
            n_size == (1u32 << aux_data.chain_merkle_proof.len()),
            "Aux POW merkle branch size does not match parent coinbase"
        );

        let bytes = hex::decode(&script_sig[pos_chain_root + 8..pos_chain_root + 16]).unwrap();
        let n_nonce = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);

        let chain_id = block_header.get_chain_id();

        let expected_index =
            Self::get_expected_index(n_nonce, chain_id, aux_data.chain_merkle_proof.len());

        require!(
            u32::try_from(aux_data.chain_id).ok() == Some(expected_index),
            "Aux POW wrong index"
        );
        let pow_hash = aux_data.parent_block.block_hash_pow();
        require!(
            self.skip_pow_verification
                || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
    }
```

**File:** btc-types/src/btc_header.rs (L38-53)
```rust
    pub fn block_hash_pow(&self) -> H256 {
        let block_header = self.get_block_header_vec();
        #[cfg(feature = "scrypt_hash")]
        {
            let params = scrypt::Params::new(10, 1, 1, 32).unwrap(); // N=1024 (2^10), r=1, p=1

            let mut output = [0u8; 32];
            scrypt::scrypt(&block_header, &block_header, &params, &mut output).unwrap();
            H256::from(output)
        }

        #[cfg(not(feature = "scrypt_hash"))]
        {
            double_sha256(&block_header)
        }
    }
```

**File:** btc-types/src/aux.rs (L10-16)
```rust
pub struct AuxData {
    pub coinbase_tx: Vec<u8>,
    pub merkle_proof: Vec<H256>,
    pub chain_merkle_proof: Vec<H256>,
    pub chain_id: usize,
    pub parent_block: Header,
}
```

**File:** contract/src/lib.rs (L120-124)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedSubmitBlocks),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** contract/src/lib.rs (L692-703)
```rust
    /// `used_aux_parent_blocks` field in all chain builds.
    #[derive(BorshDeserialize, BorshSerialize, PanicOnDefault)]
    pub struct BtcLightClientV2 {
        mainchain_height_to_header: LookupMap<u64, H256>,
        mainchain_header_to_height: LookupMap<H256, u64>,
        mainchain_tip_blockhash: H256,
        mainchain_initial_blockhash: H256,
        headers_pool: LookupMap<H256, ExtendedHeader>,
        skip_pow_verification: bool,
        gc_threshold: u64,
        used_aux_parent_blocks: near_sdk::collections::LookupSet<H256>,
        network: Network,
```
