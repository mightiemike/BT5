### Title
`PriceVelocityGuardExtension` Uses `block.number` for Velocity Accounting, Rendering the Guard Ineffective on Fast L2s — (File: `metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension` measures elapsed time between price observations using `block.number`. On L2 networks with sub-second block times (Arbitrum Nitro ~250 ms/block, zkSync ~1 s/block), `blockDiff` accumulates far faster than on Ethereum mainnet (~12 s/block). Because the allowed price movement scales as `maxChangePerBlockE18 × √(1 + blockDiff)`, the guard becomes proportionally more permissive per unit of real time on fast L2s, effectively disabling the velocity cap that pool admins configure to protect LPs from rapid oracle-price manipulation.

---

### Finding Description

The guard stores the last observed mid-price and the block at which it was recorded: [1](#0-0) 

On every `beforeSwap` call it computes: [2](#0-1) 

The invariant the guard enforces is:

```
changeE18² ≤ maxChangePerBlockE18² × (1 + blockDiff)
```

`blockDiff` is the raw difference in `block.number` values. On Ethereum mainnet one block ≈ 12 s, so `blockDiff = 1` between two consecutive swaps. On Arbitrum Nitro one block ≈ 0.25 s, so in the same 12-second window `blockDiff ≈ 48`. The allowed price movement therefore scales by `√49 ≈ 7×` relative to what the admin intended for a 12-second window. On zkSync (≈1 s/block) the factor is `√13 ≈ 3.6×`.

A pool admin who calibrates `maxChangePerBlockE18` for mainnet semantics (e.g., 1 % per block = 1 % per 12 s) inadvertently permits ≈ 7 % price movement per 12 seconds on Arbitrum Nitro — essentially no protection against rapid oracle-price manipulation.

The protocol is explicitly multi-chain (Arbitrum, zkSync, and others are named deployment targets in the README and `smart-contracts-poc`). The same extension contract and the same admin-configured parameter are expected to operate across all of these chains.

---

### Impact Explanation

When the velocity guard is bypassed:

- A manipulated or flash-crashed oracle price passes the `beforeSwap` check.
- The pool executes swaps at the bad price, paying out more of the LP reserve than the oracle-anchored curve permits.
- LPs suffer direct loss of principal (token0 or token1 reserves drained below what their share entitles them to).

This satisfies the **Bad-price execution** and **Swap conservation failure** impact categories: a trader receives more than the oracle/bin curve permits, or the pool fails to receive the owed input amount.

---

### Likelihood Explanation

- The protocol is deployed on Arbitrum and zkSync (fast-block L2s) by design.
- A single shared `maxChangePerBlockE18` value is set by the pool admin, who is likely to calibrate it against mainnet or a reference chain.
- No on-chain mechanism warns the admin that the parameter is chain-specific; the contract comment only says "per block" with no mention of L2 block-time differences.
- Any oracle price movement that is legitimate over 12 s on mainnet but occurs in 0.25 s on Arbitrum will pass the guard silently.

---

### Recommendation

Replace `block.number` with `block.timestamp` for the velocity accounting, and express `maxChangePerBlockE18` as a per-second rate (rename to `maxChangePerSecondE18`). The check becomes:

```solidity
uint256 secondsDiff = block.timestamp - s.lastUpdateTs;
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + secondsDiff);
```

`block.timestamp` advances at wall-clock rate on all EVM-compatible L2s (Arbitrum, Optimism, zkSync all honour it), making the guard chain-agnostic. This is exactly the fix applied to the analogous `currentBlockOriginHash` issue in the referenced upstream PR.

---

### Proof of Concept

**Setup**: Pool deployed on Arbitrum Nitro. Admin sets `maxChangePerBlockE18 = 0.01e18` (1 % per block, intended as 1 % per 12 s on mainnet).

**Step 1** – Swap at block B, mid-price = 1.00 (stored as `lastMidPriceX64`, `lastUpdateBlock = B`).

**Step 2** – 12 real seconds pass; Arbitrum produces 48 L2 blocks. `block.number = B + 48`.

**Step 3** – Oracle price moves to 1.07 (7 % increase in 12 s — a realistic flash event).

**Step 4** – Attacker (or any user) calls swap. Guard computes:
```
blockDiff  = 48
delta      = 0.07
changeE18  = 0.07e18
actualSq   = (0.07e18)² = 4.9e33
allowedSq  = (0.01e18)² × (1 + 48) = 1e32 × 49 = 4.9e33
```

`actualSq == allowedSq` → guard passes. On mainnet with `blockDiff = 1`, `allowedSq = 2e32`, and the swap would revert.

**Result**: A 7 % price spike in 12 seconds — which the guard was configured to block — passes silently on Arbitrum. Swaps execute at the manipulated price, draining LP reserves. [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L9-18)
```text
/// @title PriceVelocityGuardExtension
/// @notice Caps how fast the provided price can move between blocks, per pool.
/// @dev This extension allows the pool admin to increase security of the pool by limiting price
///      manipulation through velocity constraints. However, it assumes that the pool admin is not
///      an adversary and acts to optimize pool profitability. The pool admin must be trusted.
///
///      Allowed deviation scales as `maxChangePerBlockE18 * sqrt(1 + blockDifference)`.
///      Comparison is performed on squares to avoid an on-chain sqrt:
///        changeE18^2 <= maxChangePerBlockE18^2 * (1 + blockDiff)
///      where 1e18 = 100% (full unit).
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L53-74)
```text
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
```
