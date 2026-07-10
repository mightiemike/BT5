### Title
Missing AuxPoW Mandatory Enforcement Allows Non-AuxPoW Dogecoin Blocks to Advance `mainchain_tip_blockhash` — (`contract/src/dogecoin.rs`)

---

### Summary

`submit_block_header` for the Dogecoin feature branch treats `AuxData = None` as a valid alternative PoW path (raw scrypt hash check) with no guard that enforces AuxPoW is mandatory above the activation height. A trusted relayer can submit a Dogecoin block with `AuxData = None` and a valid scrypt PoW, and the contract will store it and advance `mainchain_tip_blockhash`, accepting a block that would be rejected by every real Dogecoin node.

---

### Finding Description

In `submit_block_header` (Dogecoin build), the PoW branch is:

```rust
if !skip_pow_verification {
    self.check_target(&block_header, &prev_block_header);

    if let Some(ref aux_data) = aux_data {
        self.check_aux(&block_header, aux_data);
    } else {
        let pow_hash = block_header.block_hash_pow();
        require!(
            U256::from_le_bytes(&pow_hash.0) <= target_from_bits(block_header.bits),
            format!("block should have correct pow")
        );
    }
}
``` [1](#0-0) 

When `aux_data` is `None`, the code falls into the `else` branch and only checks the raw scrypt hash against the target. The `BLOCK_VERSION_AUXPOW` flag check (`block_header.version & 0x100 != 0`) lives exclusively inside `check_aux`: [2](#0-1) 

It is **never reached** when `aux_data = None`. There is no height-based guard anywhere in the codebase that rejects `AuxData = None` above the Dogecoin AuxPoW activation height (~371,337). The grep for `auxpow_start_height`, `AUXPOW_START`, or any equivalent constant returns zero results.

`init_genesis` hardcodes `AuxData = None` and `skip_pow_verification = true` for all bootstrap blocks: [3](#0-2) 

This is intentional for genesis, but it means the contract is initialized with headers that carry no AuxPoW metadata, and the post-genesis submission path inherits no invariant that would require AuxPoW going forward.

`submit_blocks` (the public entry point) passes `self.skip_pow_verification` to `submit_block_header`: [4](#0-3) 

With `skip_pow_verification = false` (the recommended production setting), the `else` branch at line 181–188 is live and reachable by any trusted relayer.

---

### Impact Explanation

A trusted relayer can submit a Dogecoin block with `version = 1` (no `0x100` flag), `AuxData = None`, and a scrypt hash that satisfies the current `bits` target. The contract stores the header in `headers_pool`, increments `block_height`, accumulates `chain_work`, and advances `mainchain_tip_blockhash` to a block that is invalid on the real Dogecoin network. Any downstream consumer (bridge, proof verifier, SPV client) that trusts `mainchain_tip_blockhash` or `get_last_block_header` will operate on a fabricated chain tip.

---

### Likelihood Explanation

The entry point is the `submit_blocks` NEAR call, gated by `#[trusted_relayer]`. The methodology explicitly lists "trusted-relayer submission path with attacker-relevant data" as a valid attack surface. A compromised or malicious relayer with the `UnrestrictedSubmitBlocks` role can execute this immediately. No privileged key compromise beyond the relayer role is required.

For the PoW constraint: the difficulty of the genesis bootstrap determines the required scrypt work. The existing test fixture (`doge_init_blocks`) uses `bits = 0x1e0fffff` (minimum difficulty, target ≈ 2^236), making the scrypt preimage trivially computable. For a production deployment anchored to real Dogecoin mainnet blocks (bits ≈ 436 million), the scrypt work is computationally infeasible for a single attacker — this is the primary practical barrier. However, the missing invariant check is a structural flaw independent of current difficulty. [5](#0-4) 

---

### Recommendation

Add an explicit guard in `submit_block_header` (Dogecoin) that rejects `AuxData = None` when the block height is at or above the AuxPoW activation height (371,337 for mainnet). Concretely, after computing `block_height = 1 + prev_block_header.block_height`, require:

```rust
const AUXPOW_START_HEIGHT: u64 = 371_337;
if block_height >= AUXPOW_START_HEIGHT {
    require!(aux_data.is_some(), "AuxPoW required for Dogecoin blocks above activation height");
}
```

This should be checked **before** the `skip_pow_verification` gate so it applies even during genesis bootstrap, or alternatively enforced unconditionally in the `else` branch as a hard reject.

---

### Proof of Concept

1. Deploy the Dogecoin contract with `skip_pow_verification = false`, `network = Mainnet`, genesis at height 0, and 12 bootstrap blocks all using `bits = 0x1e0fffff` (as in `doge_init_blocks()`).
2. Grant the attacker account `UnrestrictedSubmitBlocks`.
3. Construct a header with `version = 1`, `prev_block_hash = <tip hash>`, valid `time`, `bits = 0x1e0fffff`, and mine a `nonce` such that `scrypt(header) <= target_from_bits(0x1e0fffff)` (trivially satisfiable; any nonce near 0 will work at this difficulty).
4. Call `submit_blocks` with `headers = [(crafted_header, None)]`.
5. Assert `get_last_block_header().block_height == 13` and `mainchain_tip_blockhash == crafted_header.block_hash()`.

The block carries no AuxPoW, has `version & 0x100 == 0`, and would be rejected by every real Dogecoin node — yet the contract accepts it and advances the canonical tip. [6](#0-5)

### Citations

**File:** contract/src/dogecoin.rs (L58-61)
```rust
        require!(
            block_header.version & BLOCK_VERSION_AUXPOW != 0,
            "Aux POW block does not have AuxPoW flag set in version"
        );
```

**File:** contract/src/dogecoin.rs (L166-204)
```rust
    pub(crate) fn submit_block_header(
        &mut self,
        header: (Header, Option<AuxData>),
        skip_pow_verification: bool,
    ) {
        let (block_header, aux_data) = header;

        let prev_block_header = self.get_prev_header(&block_header);
        let current_block_hash = block_header.block_hash();

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
        }

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
    }
```

**File:** contract/src/lib.rs (L177-179)
```rust
        for header in headers {
            self.submit_block_header(header, self.skip_pow_verification);
        }
```

**File:** contract/src/lib.rs (L480-484)
```rust
        for block_header in submit_blocks {
            #[cfg(feature = "dogecoin")]
            self.submit_block_header((block_header, None), true);
            #[cfg(not(feature = "dogecoin"))]
            self.submit_block_header(block_header, true);
```

**File:** contract/tests/test_dogecoin.rs (L53-70)
```rust
    fn doge_init_blocks() -> Vec<Header> {
        let mut blocks = Vec::new();
        let mut prev_hash = H256::default();
        let mut time = 1_500_000_000u32;
        for _ in 0..12 {
            let h = Header {
                version: 1,
                prev_block_hash: prev_hash,
                merkle_root: H256::default(),
                time,
                bits: DOGE_BITS,
                nonce: 0,
            };
            prev_hash = h.block_hash();
            time += 60;
            blocks.push(h);
        }
        blocks
```
