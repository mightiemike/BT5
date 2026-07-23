### Title
`PriceVelocityGuardExtension` Allows Unbounded Price Moves After Inactivity — Guard Scales as `sqrt(blockDiff)` but Is Named and Documented as a Strict Per-Block Cap - (File: `metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension` is documented via `@notice` as "Caps how fast the provided price can move between blocks" and its sole configurable parameter is named `maxChangePerBlockE18`. However, the actual enforcement formula is `allowedSq = maxChange² × (1 + blockDiff)`, meaning the allowed price deviation grows as `maxChange × √(1 + blockDiff)`. After a long period of pool inactivity, `blockDiff` becomes arbitrarily large, and the guard permits price moves that are orders of magnitude larger than the admin-configured cap. Any unprivileged actor can exploit this by timing a swap after many idle blocks, executing at a price the guard was supposed to block.

---

### Finding Description

The `@notice` and parameter name both assert a strict per-block cap:

```
/// @notice Caps how fast the provided price can move between blocks, per pool.
``` [1](#0-0) 

The parameter stored and emitted is `maxChangePerBlockE18`: [2](#0-1) 

But the enforcement in `beforeSwap` computes:

```solidity
uint256 blockDiff = block.number - prevBlock;
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
if (actualSq > allowedSq) revert PriceVelocityExceeded(...);
``` [3](#0-2) 

The allowed deviation is therefore `maxChange × √(1 + blockDiff)`, not `maxChange` per block. The `@dev` comment acknowledges the sqrt formula, but the `@notice` and parameter name do not — creating the same dimension/semantic mismatch as the Yearn `strategy_rateLimit` bug. A pool admin who reads the public interface and sets `maxChangePerBlockE18 = 5%` to enforce a 5%-per-block cap will actually enforce:

| Blocks since last swap | Effective allowed move |
|---|---|
| 0 | 5% |
| 3 | ~10% |
| 99 | 50% |
| 9 999 | 500% |

The state update (`s.lastMidPriceX64 = midPrice; s.lastUpdateBlock = block.number`) happens **before** the check, so the guard resets on every swap regardless of whether it reverts: [4](#0-3) 

This means a pool that is idle for many blocks has its guard silently relaxed to an unbounded envelope.

---

### Impact Explanation

The velocity guard is the primary on-chain defense against oracle price manipulation reaching live swaps. When the guard is bypassed, a swap executes at a price the pool admin intended to block. LPs bear the loss: the pool's bin liquidity is consumed at a manipulated or stale price, and the counterparty (attacker or arbitrageur) extracts the difference. This matches the allowed impact class **"Bad-price execution: stale, inverted, unbounded, or unclamped bid/ask quote reaches a pool swap."**

---

### Likelihood Explanation

Any pool that experiences a period of low swap activity (common for new pools, low-volume pairs, or during market downturns) will accumulate a large `blockDiff`. The attacker needs only to:
1. Observe that `block.number - lastUpdateBlock` is large enough to widen the envelope to cover the current oracle price deviation.
2. Call `pool.swap(...)` directly or through the router — no special role required.

The trigger is fully unprivileged and requires no oracle admin access.

---

### Recommendation

Rename the parameter to `maxChangePerSqrtBlockE18` (or similar) to match the actual formula, and update the `@notice` to accurately describe the sqrt-of-time scaling. Alternatively, if a strict per-block cap is the intended invariant, change the formula to:

```solidity
uint256 allowedSq = uint256(maxChange) * uint256(maxChange);
// enforce per-block: blockDiff does not widen the envelope
```

and separately allow the admin to configure a "grace period" multiplier explicitly. Either way, the semantic contract exposed to pool admins must match the enforcement math.

---

### Proof of Concept

1. Deploy a pool with `PriceVelocityGuardExtension`, set `maxChangePerBlockE18 = 5e16` (5%).
2. Execute one swap to seed `lastMidPriceX64 = P₀` and `lastUpdateBlock = B₀`.
3. Advance 9 999 blocks without any swap (`vm.roll(B₀ + 9999)`).
4. Oracle price moves to `P₁ = 6 × P₀` (500% change).
5. Call `pool.swap(...)`:
   - `blockDiff = 9999`
   - `changeE18 = 5e18` (500%)
   - `actualSq = 25e36`
   - `allowedSq = (5e16)² × 10000 = 25e32 × 10000 = 25e36`
   - `actualSq <= allowedSq` → **guard passes**, swap executes at the 500%-moved price.
6. LPs receive far less than fair value; the attacker or arbitrageur captures the spread.

The guard configured for 5%-per-block silently permits a 500% price move after ~10 000 idle blocks, directly contradicting the `@notice` and parameter name.

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L10-10)
```text
/// @notice Caps how fast the provided price can move between blocks, per pool.
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L24-26)
```text
  function setMaxChangePerBlock(address pool_, uint64 newMaxPctChangePerBlockE18) external onlyPoolAdmin(pool_) {
    priceVelocityState[pool_].maxChangePerBlockE18 = newMaxPctChangePerBlockE18;
    emit MaxChangePerBlockSet(pool_, newMaxPctChangePerBlockE18);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L57-58)
```text
    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L63-74)
```text
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

        if (actualSq > allowedSq) {
          revert PriceVelocityExceeded(actualSq, allowedSq);
        }
```
