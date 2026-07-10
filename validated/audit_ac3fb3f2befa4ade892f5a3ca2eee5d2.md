### Title
AuxPoW Parent Block PoW Verified Against Dogecoin Child Block's Difficulty Instead of Parent Block's Own Difficulty — (File: `contract/src/dogecoin.rs`)

---

### Summary

In `check_aux`, the parent block's PoW hash is compared against `target_from_bits(block_header.bits)` — the **Dogecoin child block's** difficulty target — rather than `target_from_bits(aux_data.parent_block.bits)` — the **parent block's own** difficulty target. This is a direct analog to the ENS "check performed on the wrong entity" class: a validation meant to enforce the parent block's PoW is instead applied using the wrong block's parameters, allowing a parent block with insufficient real mining work to pass.

---

### Finding Description

In AuxPoW (Auxiliary Proof of Work), the parent block (typically a Bitcoin block) is the entity that performs the actual mining. Its hash must satisfy the parent block's own declared difficulty target, encoded in `aux_data.parent_block.bits`. The Dogecoin child block's `bits` field encodes only the Dogecoin network's difficulty, which is a completely separate value.

In `check_aux` at `contract/src/dogecoin.rs` lines 149–154:

```rust
let pow_hash = aux_data.parent_block.block_hash_pow();
require!(
    self.skip_pow_verification
        || U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
    format!("block should have correct pow")
);
```

`pow_hash` is the hash of `aux_data.parent_block`, but the target it is checked against is `target_from_bits(block_header.bits)` — the **Dogecoin child block's** difficulty. The correct check must use `target_from_bits(aux_data.parent_block.bits)`. [1](#0-0) 

The `check_target` call earlier in `submit_block_header` only validates that `block_header.bits` is the correct Dogecoin network difficulty for the current epoch — it says nothing about the parent block's difficulty. [2](#0-1) 

The `AuxData` struct carries `parent_block: Header`, which has its own `bits` field that encodes the parent block's declared difficulty target. This field is never used in the PoW check. [3](#0-2) 

---

### Impact Explanation

Bitcoin's difficulty is typically orders of magnitude harder than Dogecoin's. `target_from_bits(block_header.bits)` (Dogecoin target) is a much larger number (easier threshold) than `target_from_bits(aux_data.parent_block.bits)` (Bitcoin target). An attacker can craft a parent block whose hash satisfies Dogecoin's easy threshold but not Bitcoin's hard threshold. The PoW check passes, and the contract accepts a Dogecoin block backed by a parent block with no real Bitcoin mining work behind it. This corrupts the canonical chain: the accepted block's `chain_work` is computed from `block_header.bits` (the Dogecoin difficulty), so a chain of such fake-AuxPoW blocks can accumulate enough chainwork to trigger a reorg and become the canonical chain. [4](#0-3) 

---

### Likelihood Explanation

The attacker entry path is `submit_blocks`, which is callable by any unprivileged NEAR account (subject only to the `trusted_relayer` gate, which can be bypassed by accounts with `UnrestrictedSubmitBlocks` role, or if the relayer stake mechanism is not enforced). The attacker must:

1. Construct a Dogecoin header with the AuxPoW version flag set.
2. Craft a parent block whose double-SHA256 hash falls below Dogecoin's current difficulty target (far easier than Bitcoin's).
3. Embed the Dogecoin block hash in a coinbase transaction and build a valid Merkle proof.
4. Pass the remaining structural checks in `check_aux` (chain merkle root in coinbase, chain ID, nonce index).

Steps 2–4 require non-trivial but feasible effort. The PoW mining required is at Dogecoin difficulty, not Bitcoin difficulty, making it computationally accessible to a well-resourced attacker.

---

### Recommendation

Replace `block_header.bits` with `aux_data.parent_block.bits` in the PoW check inside `check_aux`:

```rust
// Before (wrong):
U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits)

// After (correct):
U256::from_le_bytes(&pow_hash.0) <= target_from_bits(aux_data.parent_block.bits)
``` [1](#0-0) 

---

### Proof of Concept

1. Attacker calls `submit_blocks` with a `(Header, Some(AuxData))` pair where `Header.version` has bit `0x100` set (AuxPoW flag) and `Header.get_chain_id()` returns the expected Dogecoin chain ID (`0x0062`).
2. `AuxData.parent_block` is a crafted header whose `block_hash_pow()` (double-SHA256) is below `target_from_bits(block_header.bits)` (Dogecoin difficulty) but above `target_from_bits(aux_data.parent_block.bits)` (Bitcoin difficulty). This requires mining at Dogecoin difficulty only.
3. `AuxData.coinbase_tx` is a transaction whose `script_sig` contains the chain merkle root of the Dogecoin block hash, satisfying the `pos_chain_root` and `n_size`/`n_nonce` checks.
4. `check_aux` reaches line 149, computes `pow_hash = aux_data.parent_block.block_hash_pow()`, and evaluates `pow_hash <= target_from_bits(block_header.bits)` — which is `true` by construction.
5. The block is accepted and stored in `headers_pool`, with its `chain_work` accumulated from `block_header.bits`. Repeated submissions build a fake-AuxPoW chain that can surpass the real chain's `chain_work` and trigger `reorg_chain`, replacing the canonical chain. [5](#0-4) [6](#0-5)

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

**File:** contract/src/dogecoin.rs (L176-188)
```rust
        if !skip_pow_verification {
            self.check_target(&block_header, &prev_block_header);

            if let Some(ref aux_data) = aux_data {
                self.check_aux(&block_header, aux_data);
            } else {
                let pow_hash = block_header.block_hash_pow();
                // Check if the block hash is less than or equal to the target
                require!(
                    U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
                    format!("block should have correct pow")
                );
            }
```

**File:** contract/src/dogecoin.rs (L191-203)
```rust
        let (current_block_computed_chain_work, overflow) = prev_block_header
            .chain_work
            .overflowing_add(work_from_bits(block_header.bits));
        require!(!overflow, "Addition of U256 values overflowed");

        let current_header = ExtendedHeader {
            block_header: block_header.clone().into_light(),
            block_hash: current_block_hash,
            chain_work: current_block_computed_chain_work,
            block_height: 1 + prev_block_header.block_height,
        };

        self.submit_block_header_inner(current_header, &prev_block_header);
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

**File:** contract/src/lib.rs (L560-567)
```rust
            self.store_fork_header(&current_header);

            // Current chainwork is higher than on a current mainchain, let's promote the fork
            if current_header.chain_work > total_main_chain_chainwork {
                log!("Chain reorg");
                self.reorg_chain(current_header, last_main_chain_block_height);
            }
        }
```
