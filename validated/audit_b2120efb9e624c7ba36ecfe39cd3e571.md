### Title
Genesis `chain_work` Initialized to Single-Block Work Instead of Cumulative Chain Work — (`contract/src/lib.rs`)

### Summary

In `init_genesis`, the genesis block's `chain_work` is set to the work of only that single block (`work_from_bits(block_header.bits)`), not the cumulative proof-of-work representing all blocks from height 0 up to the chosen genesis height. Because `chain_work` is the sole fork-choice metric, this incorrect initialization permanently lowers the bar an attacker must clear to reorg the contract's canonical chain.

### Finding Description

`init_genesis` (contract/src/lib.rs, lines 420–485) initializes the anchor block as follows:

```rust
let chain_work = work_from_bits(block_header.bits);   // line 466

let header = ExtendedHeader {
    block_header: block_header.into_light(),
    block_height,
    block_hash: current_block_hash.clone(),
    chain_work,                                        // line 471
};
self.store_block_header(&header);
```

`work_from_bits` converts a single block's `bits` field into the expected number of hashes for that one block. For a real deployment at, say, height 685,440, the true cumulative chain work is the sum of the per-block work of all 685,440 preceding blocks — orders of magnitude larger.

Every subsequent block submitted via `submit_block_header` adds its own per-block work on top of this baseline. The fork-choice rule selects the chain with the highest `chain_work`. Because the baseline is set to ≈1 block's worth of work instead of ≈685,440 blocks' worth, the entire fork-choice comparison is anchored to a value that is astronomically too low.

This is a direct analog to the Beanstalk `InitBipSeedGauge` issue: an initialization routine assigns a value derived from a per-unit metric (work of one block / stalk earned per season) when it should use the proportional cumulative metric (total chain work / total deposited BDV). The consequence in both cases is that the system's core ordering invariant starts from a wrong baseline that takes a very long time to self-correct.

### Impact Explanation

The fork-choice rule is corrupted from genesis. After the contract is initialized and the legitimate chain has grown by N blocks, the legitimate tip's `chain_work` ≈ N+1 blocks' worth of work (not 685,440+N). An attacker who can produce a competing chain of N+2 valid PoW blocks rooted at the same genesis hash will have higher `chain_work` and will be accepted as the canonical chain. The attacker needs to overcome only N+1 blocks of work — not the true 685,440+N blocks of accumulated Bitcoin proof-of-work. This allows a realistic reorg attack that would be computationally infeasible if the genesis `chain_work` were correctly initialized. The corrupted canonical chain directly affects `verify_transaction_inclusion` results returned to downstream dApps.

### Likelihood Explanation

Medium. The attacker must submit valid PoW blocks (when `skip_pow_verification = false`). However, the required work is reduced from the full Bitcoin chain work (≈10^24 hashes at height 685,440) to just a few blocks' worth of work above the legitimate chain's post-genesis accumulation. Real orphaned Bitcoin blocks or a chain with matching difficulty can be sourced without mining from scratch. The attack window is permanent — the incorrect baseline never self-corrects because `chain_work` only grows additively from the wrong starting point.

### Recommendation

Pass the true cumulative chain work at the genesis height as a caller-supplied `InitArgs` field (analogous to how Bitcoin SPV clients require the caller to attest to the chain work at the checkpoint). Validate that the supplied value is consistent with the genesis block's `bits` field (i.e., ≥ `work_from_bits(genesis.bits)`). Use this value as the starting `chain_work` instead of computing it from a single block:

```rust
// In InitArgs, add:
// pub genesis_chain_work: U256,

// In init_genesis, replace:
let chain_work = work_from_bits(block_header.bits);
// with:
let chain_work = args.genesis_chain_work;
require!(chain_work >= work_from_bits(block_header.bits), "genesis_chain_work too low");
```

### Proof of Concept

1. Deploy the contract with `genesis_block_height = 685_440` and a real Bitcoin block at that height. The genesis `chain_work` is set to `work_from_bits(685440_block.bits)` ≈ 1 block's work.
2. The relayer submits 10 legitimate blocks (heights 685,441–685,450). The mainchain tip now has `chain_work` ≈ 11 blocks' work.
3. An attacker submits 12 valid Bitcoin blocks (e.g., real orphaned blocks from a competing fork rooted at the same genesis hash). Their fork tip has `chain_work` ≈ 12 blocks' work > 11.
4. The contract accepts the attacker's fork as the new canonical chain.
5. `verify_transaction_inclusion` now verifies transactions against the attacker's fork, not the real Bitcoin mainchain.

The root cause is at: [1](#0-0) 

where `chain_work` is assigned from a single-block computation rather than the true cumulative work at the genesis height, and at: [2](#0-1) 

where `InitArgs` provides no field for the caller to supply the correct cumulative chain work.

### Citations

**File:** contract/src/lib.rs (L135-161)
```rust
    pub fn init(args: InitArgs) -> Self {
        let mut contract = Self {
            mainchain_height_to_header: LookupMap::new(StorageKey::MainchainHeightToHeader),
            mainchain_header_to_height: LookupMap::new(StorageKey::MainchainHeaderToHeight),
            headers_pool: LookupMap::new(StorageKey::HeadersPool),
            mainchain_initial_blockhash: H256::default(),
            mainchain_tip_blockhash: H256::default(),
            skip_pow_verification: args.skip_pow_verification,
            gc_threshold: args.gc_threshold,
            network: args.network,
        };

        // Make the contract itself super admin. This allows us to grant any role in the
        // constructor.
        near_sdk::require!(
            contract.acl_init_super_admin(env::current_account_id()),
            "Failed to initialize super admin",
        );

        contract.init_genesis(
            &args.genesis_block_hash,
            args.genesis_block_height,
            args.submit_blocks,
        );

        contract
    }
```

**File:** contract/src/lib.rs (L466-473)
```rust
        let chain_work = work_from_bits(block_header.bits);

        let header = ExtendedHeader {
            block_header: block_header.into_light(),
            block_height,
            block_hash: current_block_hash.clone(),
            chain_work,
        };
```
