### Title
`PriceVelocityGuardExtension` per-block velocity cap is bypassed by multiple intra-block swaps because `lastMidPriceX64` is overwritten on every swap instead of being anchored to the block's first observation — (File: `metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` unconditionally overwrites `lastMidPriceX64` and `lastUpdateBlock` on every swap, even when multiple swaps occur within the same block. Each intra-block swap is therefore checked against the immediately preceding swap's mid price rather than the block's starting mid price. When the oracle price changes between swaps within the same block (possible with Pyth-style push oracles where anyone can submit a signed price update), an attacker can chain multiple swaps that each individually satisfy the velocity constraint but collectively exceed the configured per-block limit.

---

### Finding Description

In `beforeSwap`, the guard reads `prevMid` and `prevBlock`, then immediately overwrites both in storage before performing the velocity check:

```solidity
s.lastMidPriceX64 = midPrice;          // written before check
s.lastUpdateBlock = uint64(block.number); // written before check

if (prevMid != 0) {
    uint64 maxChange = s.maxChangePerBlockE18;
    if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;
        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);
        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);
        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
        if (actualSq > allowedSq) revert PriceVelocityExceeded(actualSq, allowedSq);
    }
}
``` [1](#0-0) 

When `block.number == prevBlock` (same block as the last swap), `blockDiff = 0` and `allowedSq = maxChange² × 1`. The check is performed against `prevMid`, which is the mid price from the most recent swap in this block — not the mid price at the start of the block.

This is the direct analog of the external bug: just as multiple `UserLock` entries with the same `fromBlock` cause binary search to return the wrong history entry, multiple swaps in the same block each update `lastMidPriceX64`, causing the velocity check to use the wrong reference price. The invariant "price cannot move more than `maxChangePerBlockE18` per block" degrades to "price cannot move more than `maxChangePerBlockE18` per swap," allowing `n × maxChangePerBlockE18` total movement within a single block.

**Attack path (Pyth oracle, permissionless price submission):**

1. Pool is configured with `maxChangePerBlockE18 = 0.01e18` (1% per block). Initial state: `lastMidPriceX64 = P0`, `lastUpdateBlock = B-1`.
2. In block B, attacker submits a valid signed Pyth update moving the price to `P1 = P0 × 1.01`.
3. Swap 1: `prevMid = P0`, `blockDiff = 0`. Check: `(0.01e18)² ≤ (0.01e18)² × 1`. Passes. Guard writes `lastMidPriceX64 = P1`.
4. Attacker submits another valid signed Pyth update moving the price to `P2 = P1 × 1.01 ≈ P0 × 1.0201`.
5. Swap 2: `prevMid = P1`, `blockDiff = 0`. Check: `(0.01e18)² ≤ (0.01e18)² × 1`. Passes. Guard writes `lastMidPriceX64 = P2`.
6. Net: price moved ~2.01% in block B against a 1% cap. Both swaps executed at prices the guard was supposed to block.

The pool's `swap` function passes `msg.sender` as `sender` and the live oracle bid/ask to `_beforeSwap`, which dispatches to the extension: [2](#0-1) 

The extension receives `bidPriceX64` and `askPriceX64` from the live oracle and derives `midPrice` from them: [3](#0-2) 

---

### Impact Explanation

The velocity guard's configured per-block price-movement cap is not enforced when the oracle delivers multiple price updates within a single block. Swaps execute at prices that the guard was supposed to block, constituting bad-price execution against LP positions. LPs suffer losses from trades at prices outside the intended velocity envelope — a direct loss of LP principal that the extension was deployed to prevent.

---

### Likelihood Explanation

Pyth oracles are push-based: anyone holding a valid signed price attestation (publicly available from Pyth's price service) can submit an update. An attacker can obtain two consecutive signed price updates (each within the velocity limit) and submit them in the same block, interleaved with swaps. No privileged access is required. The only constraint is that the oracle data must be valid (signed by Pyth), which is satisfied by using real market price data. This is a realistic, low-barrier attack on any pool that uses a Pyth-backed price provider and has `PriceVelocityGuardExtension` configured.

---

### Recommendation

Adopt the same fix as the external bug: when `block.number == lastUpdateBlock`, do not overwrite `lastMidPriceX64`. Keep the block's first observed mid price as the reference for all swaps within the same block:

```solidity
PriceVelocityState storage s = priceVelocityState[pool_];
uint128 prevMid = s.lastMidPriceX64;
uint64 prevBlock = s.lastUpdateBlock;

// Only advance the reference price when entering a new block.
// Within the same block, keep prevMid as the block-start anchor.
if (block.number > prevBlock) {
    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
}

if (prevMid != 0) {
    // ... velocity check unchanged ...
}
```

This mirrors the external bug's recommended fix ("modify last entry in array instead of pushing new one if `fromBlock == block.number`") and ensures the per-block cap is measured from the block's first observed price, not the most recent swap's price.

---

### Proof of Concept

```solidity
// Setup: pool with PriceVelocityGuardExtension, maxChangePerBlockE18 = 0.01e18 (1%)
// Oracle: Pyth-backed, updatable within a block

// Block B-1: some swap sets lastMidPriceX64 = P0, lastUpdateBlock = B-1

// Block B:
// Step 1: Submit Pyth update → oracle now quotes bid/ask around P1 = P0 * 1.01
pythOracle.updatePriceFeeds(signedUpdate1); // P1 = P0 * 1.01

// Step 2: Swap 1 — velocity check: |P1-P0|/P0 = 1% ≤ 1%. PASSES.
// Guard writes lastMidPriceX64 = P1, lastUpdateBlock = B.
pool.swap(recipient, zeroForOne, amount, priceLimit, "", "");

// Step 3: Submit another Pyth update → oracle now quotes around P2 = P1 * 1.01
pythOracle.updatePriceFeeds(signedUpdate2); // P2 ≈ P0 * 1.0201

// Step 4: Swap 2 — velocity check: |P2-P1|/P1 = 1% ≤ 1%. PASSES.
// Guard writes lastMidPriceX64 = P2, lastUpdateBlock = B.
pool.swap(recipient, zeroForOne, amount, priceLimit, "", "");

// Result: price moved 2.01% in block B against a 1% cap.
// Both swaps executed. Guard never reverted.
// Expected: Swap 2 should have reverted with PriceVelocityExceeded.
``` [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-79)
```text
  function beforeSwap(
    address,
    address,
    bool,
    int128,
    uint128,
    uint256,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata
  ) external override returns (bytes4) {
    address pool_ = msg.sender;
    (uint256 midPriceX64,) = SwapMath.midAndSpreadFeeX64FromBidAsk(uint256(bidPriceX64), uint256(askPriceX64));
    // casting to `uint128` is safe: geometric mid of two uint128 bid/ask quotes fits uint128 (same bound as pool)
    // forge-lint: disable-next-line(unsafe-typecast)
    uint128 midPrice = uint128(midPriceX64);

    PriceVelocityState storage s = priceVelocityState[pool_];
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

    if (prevMid != 0) {
      uint64 maxChange = s.maxChangePerBlockE18;
      if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

        if (actualSq > allowedSq) {
          revert PriceVelocityExceeded(actualSq, allowedSq);
        }
      }
    }

    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```
