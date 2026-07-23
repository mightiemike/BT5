### Title
`block.number` Returns L1 Ancestor Block on Arbitrum, Breaking `PriceVelocityGuardExtension` Velocity Guard — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` uses `block.number` to compute `blockDiff`, which scales the allowed oracle-price movement between swaps. On Arbitrum, `block.number` returns the **L1 ancestor block number** (advances ~every 12 s) rather than the L2 block number (advances ~every 0.25 s). Because ~48 L2 blocks are produced per L1 block, `blockDiff` is `0` for every swap after the first one within the same L1 block. This collapses the guard's time-scaling factor to `1` regardless of how many L2 blocks have elapsed, producing two fund-impacting failure modes depending on how `maxChangePerBlockE18` is calibrated.

---

### Finding Description

The velocity guard formula is:

```
allowedSq = maxChangePerBlockE18² × (1 + blockDiff)
``` [1](#0-0) 

`blockDiff` is computed as:

```solidity
uint256 blockDiff = block.number - prevBlock;
```

and `lastUpdateBlock` is written as:

```solidity
s.lastUpdateBlock = uint64(block.number);
``` [2](#0-1) 

On Arbitrum, `block.number` is the **L1 ancestor block**, not the L2 block. With ~48 L2 blocks per L1 block, every swap after the first within a single L1 epoch sees `prevBlock == block.number`, so `blockDiff = 0` and `allowedSq = maxChangePerBlockE18²` — a constant, regardless of elapsed L2 time.

There is no chain-specific block-number override anywhere in the codebase.

**Failure mode A — DoS of legitimate swaps (calibrated for L2 blocks):**
A pool admin deploying on Arbitrum naturally calibrates `maxChangePerBlockE18` for the 0.25 s L2 block cadence (e.g., 0.1 % per L2 block). On Arbitrum the guard enforces that same 0.1 % limit per L1 block (12 s). Normal market volatility of, say, 0.3 % over 0.75 s (3 L2 blocks, still within one L1 block) causes `actualSq > allowedSq` and the guard reverts with `PriceVelocityExceeded`, blocking every subsequent swap in that L1 window. The pool becomes effectively unusable for the duration of the L1 block.

**Failure mode B — Guard bypass / LP value drain (calibrated for L1 blocks):**
If the admin instead calibrates for L1 blocks (e.g., 1 % per 12 s), each of the 48 L2-block swaps within one L1 block is individually allowed to move the oracle mid by 1 % (since `blockDiff = 0` resets the budget on every swap). A sequence of 48 swaps can therefore shift the oracle-derived mid by up to 48 % within a single L1 block — 48× the intended cap — while the guard never fires. This defeats the guard's purpose of protecting LPs from rapid oracle-price drift and allows adversarial or MEV-driven price walks that the extension was deployed to prevent.

---

### Impact Explanation

- **Failure mode A**: Core swap functionality is rendered unusable on Arbitrum for any pool using this extension with L2-calibrated parameters. LPs cannot earn fees; traders cannot execute. This is a direct broken-core-functionality impact.
- **Failure mode B**: The oracle-velocity guard — the sole mechanism protecting LP principal from rapid oracle-price manipulation — is bypassed on Arbitrum. LPs can be drained at prices far outside the intended guard envelope, constituting a direct loss of LP principal.

---

### Likelihood Explanation

Arbitrum is a primary EVM deployment target. The miscalibration is silent — no revert, no event — and the correct L2 block number is not surfaced by `block.number` on any Arbitrum chain. Any pool admin who sets `maxChangePerBlockE18` based on Arbitrum's 0.25 s L2 block time (the natural, documented block cadence) triggers failure mode A immediately. Failure mode B is reachable by any actor who can submit 48 swaps across 48 consecutive L2 blocks within one L1 epoch, which requires no special privilege.

---

### Recommendation

Replace `block.number` with a chain-aware block-number helper that calls the ArbSys precompile (`address(0x64).call(abi.encodeWithSignature("arbBlockNumber()"))`) on Arbitrum and falls back to `block.number` on other chains, mirroring the fix applied in Bunni v2 PR #99. Alternatively, replace the block-based velocity formula entirely with a `block.timestamp`-based one, which is correct on all EVM chains without chain-specific branching.

---

### Proof of Concept

**Setup**: Arbitrum mainnet. `PriceVelocityGuardExtension` deployed. Pool configured with `maxChangePerBlockE18 = 1e15` (0.1 % per block, calibrated for 0.25 s L2 blocks).

**Failure mode A — DoS**:

1. Swap S1 executes at L2 block 1000, L1 block 50. Oracle mid = 1.000. Guard writes `lastMidPriceX64 = 1.000`, `lastUpdateBlock = 50`.
2. Oracle mid moves to 1.002 (0.2 % — normal 0.5 s market move).
3. Swap S2 executes at L2 block 1002, L1 block 50 (same L1 block). `blockDiff = 50 − 50 = 0`. `allowedSq = (1e15)² × 1`. `changeE18 = 2e15`. `actualSq = (2e15)² = 4e30 > 1e30 = allowedSq`. Guard reverts `PriceVelocityExceeded`. Every subsequent swap in this L1 window is similarly blocked.

**Failure mode B — bypass**:

1. Admin sets `maxChangePerBlockE18 = 1e16` (1 % per L1 block).
2. Adversary submits 48 swaps across L2 blocks 1000–1047, all within L1 block 50. Each swap moves the oracle mid by 1 %. `blockDiff = 0` for swaps 2–48; each passes the guard individually.
3. Cumulative oracle-mid shift after 48 swaps: ~48 %. The guard never fires. LPs trade against a price 48× outside the intended velocity cap. [3](#0-2)

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
