### Title
Missing Chain-Tip Freshness Check in Transaction Inclusion Verification — (`contract/src/lib.rs`)

### Summary

`verify_transaction_inclusion` and `verify_transaction_inclusion_v2` confirm SPV proofs against the contract's stored mainchain tip without ever verifying that the tip's timestamp is recent. If the relayer stops submitting headers, the contract silently operates on a stale view of the Bitcoin chain and continues returning `true` for proofs that may no longer be valid on the real chain.

### Finding Description

Both verification entry points retrieve the stored mainchain tip and use its block height to count confirmations, but neither compares the tip's `block_header.time` against the current NEAR block time. [1](#0-0) 

The `heaviest_block_header` carries a `block_header.time` field (the Bitcoin miner-set timestamp stored in `LightHeader`): [2](#0-1) 

That timestamp is stored verbatim in `ExtendedHeader.block_header`: [3](#0-2) 

The submission path (`check_pow`) does enforce a freshness bound on incoming headers — it rejects any header whose `time` exceeds `env::block_timestamp_ms() / 1000 + MAX_FUTURE_BLOCK_TIME_LOCAL`: [4](#0-3) 

But that guard only runs during `submit_blocks`. Once headers are stored, the verification functions never re-examine the tip's timestamp. There is no equivalent check of the form:

```
require!(
    current_near_time <= tip.block_header.time + FRESHNESS_THRESHOLD,
    "light client tip is stale"
);
```

in either `verify_transaction_inclusion`: [5](#0-4) 

or `verify_transaction_inclusion_v2`: [6](#0-5) 

### Impact Explanation

Any external NEAR contract that calls `verify_transaction_inclusion[_v2]` and acts on the boolean result (e.g., releasing funds, minting tokens, or unlocking a bridge) can be deceived when the light client's tip is stale. A Bitcoin block that was part of the contract's mainchain at the time of submission may have been reorganized away on the real Bitcoin network. Because the contract's tip has not advanced, it still considers that block canonical and returns `true` for a Merkle proof against it. The recipient contract has no way to detect this condition from the return value alone.

**Impact: Medium** — financial loss or incorrect state transitions in downstream contracts that trust the verification result.

### Likelihood Explanation

The relayer is a single off-chain process. Network outages, bugs, or deliberate griefing can halt header submission. Bitcoin reorganizations of 1–6 blocks occur occasionally under normal conditions; deeper reorgs are rare but possible under adversarial mining. The window of exposure grows linearly with the duration of relayer downtime. No privileged access is required to call the verification functions — any NEAR account can invoke them.

**Likelihood: Medium** — relayer liveness failures are realistic operational events, not purely theoretical.

### Recommendation

Add a staleness guard at the top of both verification functions. Compare the stored tip's `block_header.time` against the current NEAR block timestamp and reject calls when the gap exceeds a configurable threshold (e.g., 2 hours, matching `MAX_FUTURE_BLOCK_TIME_LOCAL`):

```rust
let tip_time = heaviest_block_header.block_header.time;
let current_time = u32::try_from(env::block_timestamp_ms() / 1000).unwrap();
require!(
    current_time <= tip_time + STALENESS_THRESHOLD,
    "light client tip is stale: refusing to verify against outdated chain state"
);
```

Expose `STALENESS_THRESHOLD` as a configurable contract parameter so operators can tune it per chain (Bitcoin ~7200 s, Dogecoin ~3600 s, etc.).

### Proof of Concept

1. Deploy the contract with a valid Bitcoin genesis and a few initial headers.
2. Allow the relayer to submit headers up to height H with tip timestamp T.
3. Stop the relayer. Wait until the real Bitcoin chain has advanced far enough that a reorg has invalidated a block at height H−N.
4. As an unprivileged NEAR caller, invoke `verify_transaction_inclusion` with a valid Merkle proof for a transaction in the now-reorganized block, `confirmations = N`, and the block's hash.
5. The contract returns `true` because it still considers that block canonical — `mainchain_header_to_height` still maps the hash to height H−N, and the confirmation count check passes against the stale tip height.
6. A downstream bridge contract acting on this `true` result releases funds for a Bitcoin transaction that no longer exists on the canonical chain.

### Citations

**File:** contract/src/lib.rs (L288-323)
```rust
    pub fn verify_transaction_inclusion(&self, #[serializer(borsh)] args: ProofArgs) -> bool {
        require!(
            args.confirmations <= self.gc_threshold,
            "The required number of confirmations exceeds the number of blocks stored in memory"
        );

        let heaviest_block_header = self
            .headers_pool
            .get(&self.mainchain_tip_blockhash)
            .unwrap_or_else(|| env::panic_str(ERR_KEY_NOT_EXIST));
        let target_block_height = self
            .mainchain_header_to_height
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("block does not belong to the current main chain"));

        // Check requested confirmations. No need to compute proof if insufficient confirmations.
        require!(
            (heaviest_block_header.block_height).saturating_sub(target_block_height) + 1
                >= args.confirmations,
            "Not enough blocks confirmed"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(!args.merkle_proof.is_empty(), "Merkle proof is empty");

        // compute merkle tree root and check if it matches block's original merkle tree root
        merkle_tools::compute_root_from_merkle_proof(
            args.tx_id,
            usize::try_from(args.tx_index).unwrap(),
            &args.merkle_proof,
        ) == header.block_header.merkle_root
    }
```

**File:** contract/src/lib.rs (L347-369)
```rust
    pub fn verify_transaction_inclusion_v2(&self, #[serializer(borsh)] args: ProofArgsV2) -> bool {
        require!(
            args.merkle_proof.len() == args.coinbase_merkle_proof.len(),
            "Coinbase merkle proof and transaction merkle proof should have the same length"
        );

        let header = self
            .headers_pool
            .get(&args.tx_block_blockhash)
            .unwrap_or_else(|| env::panic_str("cannot find requested transaction block"));

        require!(
            merkle_tools::compute_root_from_merkle_proof(
                args.coinbase_tx_id.clone(),
                0usize,
                &args.coinbase_merkle_proof,
            ) == header.block_header.merkle_root,
            "Incorrect coinbase merkle proof"
        );

        #[allow(deprecated)]
        self.verify_transaction_inclusion(args.into())
    }
```

**File:** btc-types/src/btc_header.rs (L17-18)
```rust
    /// The timestamp of the block, as claimed by the miner.
    pub time: u32,
```

**File:** btc-types/src/header.rs (L25-37)
```rust
pub struct ExtendedHeader {
    pub block_header: LightHeader,
    /// Below, state contains additional fields not presented in the standard blockchain header
    /// those fields are used to represent additional information required for fork management
    /// and other utility functionality
    ///
    /// Current `block_hash`
    pub block_hash: H256,
    /// Accumulated chainwork at this position for this block
    pub chain_work: Work,
    /// Block height in the Bitcoin network
    pub block_height: u64,
}
```

**File:** contract/src/bitcoin.rs (L34-39)
```rust
        // Check timestamp
        let current_timestamp = u32::try_from(env::block_timestamp_ms() / 1000).unwrap(); // Convert to seconds
        require!(
            block_header.time <= current_timestamp + MAX_FUTURE_BLOCK_TIME_LOCAL,
            "time-too-new: block timestamp too far in the future"
        );
```
