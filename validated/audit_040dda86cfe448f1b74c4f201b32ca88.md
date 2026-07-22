### Title
`PriceVelocityGuardExtension::beforeSwap` velocity guard silently disabled after prolonged pool inactivity due to unbounded `blockDiff` — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension` is the Metric OMM analog of `BalancerPriceOracle::validatePrice`: it is the sole oracle-price guard for pools that configure it, and it compares a **stale stored reference price** (`lastMidPriceX64`) against the current oracle mid-price. Because the allowed-deviation budget scales as `maxChange² × (1 + blockDiff)` with no cap on `blockDiff`, any pool that goes without a swap for a sufficiently long period has its velocity guard silently rendered ineffective — any oracle price jump, however large, passes the check on the first swap after the gap.

---

### Finding Description

`PriceVelocityGuardExtension.beforeSwap` stores the oracle mid-price from the **previous swap** in `lastMidPriceX64` and the block number in `lastUpdateBlock`. On the next swap it computes:

```solidity
uint256 blockDiff = block.number - prevBlock;          // unbounded
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);
if (actualSq > allowedSq) revert PriceVelocityExceeded(...);
``` [1](#0-0) 

`blockDiff` is the raw difference between the current block and the block of the last swap. There is no ceiling. As `blockDiff` grows, `allowedSq` grows without bound, so the inequality `actualSq > allowedSq` eventually becomes impossible to satisfy regardless of how large the price jump is.

`lastMidPriceX64` is **only updated inside `beforeSwap`** (i.e., only when a swap occurs) and via the admin-only `setLastMidPrice`. [2](#0-1) 

There is no keeper, no incentive, and no automatic refresh path — exactly the same structural gap as the BalancerPriceOracle's `updatePrice()` never being called. The guard's reference price becomes stale whenever the pool is idle, and the staleness is never bounded.

Additionally, `PriceVelocityGuardExtension.beforeSwap` drops the `onlyPool` modifier that `BaseMetricExtension.beforeSwap` declares: [3](#0-2) [4](#0-3) 

Any address can call `beforeSwap` directly on the extension. Because `pool_ = msg.sender`, a caller can write arbitrary `lastMidPriceX64` / `lastUpdateBlock` values for their own address as the pool key. While this does not directly overwrite a real pool's slot (the key is `msg.sender`), it confirms the guard has no authentication boundary and the state-update path is fully open.

---

### Impact Explanation

The velocity guard is the only on-chain mechanism that caps how fast the oracle-provided bid/ask price can move between swaps for pools that configure this extension. When `blockDiff` is large enough, `allowedSq` exceeds any realistic `actualSq`, so:

- A swap executing at an oracle price that has moved 100 % (or more) from the stale reference passes the guard unchecked.
- LPs bear the full adverse-selection cost of the price jump with no protection.
- A sandwich attacker who can influence the oracle feed (e.g., via a flash-loan-driven spot move on the underlying venue that Pyth/Chainlink reflects) can time the attack to coincide with a long idle period, guaranteeing the guard will not fire.

Concrete arithmetic: with `maxChangePerBlockE18 = 1e15` (0.1 % per block) and `blockDiff = 1 000 000` blocks (~2 weeks on Ethereum):

```
allowedSq = (1e15)² × 1_000_001 = 1_000_001 × 1e30 ≈ 1.000001e36
actualSq  for a 100 % move = (1e18)² = 1e36
1e36 < 1.000001e36  →  guard passes
```

The guard allows a 100 % price jump with a configured limit of 0.1 %/block.

---

### Likelihood Explanation

- Low-volume or newly deployed pools naturally go many blocks without a swap.
- Market downturns or protocol pauses create extended idle windows on any pool.
- No privileged access is required: any user can be the first swapper after the idle period.
- The attacker does not need to manipulate the oracle; a genuine large price move that occurs during the idle window is equally effective at bypassing the guard, causing LP losses through adverse selection.

---

### Recommendation

1. **Cap `blockDiff`** at a protocol-defined maximum (e.g., `maxBlockDiff = 7200` blocks ≈ 1 day):
   ```solidity
   uint256 blockDiff = block.number - prevBlock;
   if (blockDiff > MAX_BLOCK_DIFF) {
       // Reference is too stale; skip velocity check and reset anchor.
       return IMetricOmmExtensions.beforeSwap.selector;
   }
   ```
2. **Alternatively**, if `blockDiff` exceeds the cap, treat the guard as unarmed for this swap (reset the anchor to the current price) rather than silently passing an unbounded deviation.
3. **Restore `onlyPool`** on `beforeSwap` to match the base class contract and prevent unauthenticated state writes.

---

### Proof of Concept

```
Setup:
  pool configured with PriceVelocityGuardExtension
  maxChangePerBlockE18 = 1e15  (0.1 % per block)
  lastMidPriceX64 = P  (set at block N after last swap)

Attack:
  1. Wait until block N + 1_000_000 (no swaps occur).
  2. Oracle price has moved from P to 2P (100 % increase).
  3. Call pool.swap(...) — beforeSwap fires:
       blockDiff  = 1_000_000
       delta      = P
       changeE18  = (P × 1e18) / P = 1e18
       actualSq   = 1e36
       allowedSq  = (1e15)² × 1_000_001 = 1_000_001 × 1e30 > 1e36
       → guard passes, swap executes at 2P
  4. LPs suffer full adverse-selection loss on the 100 % price move
     with zero velocity-guard protection.
```

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-47)
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
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L54-58)
```text
    uint128 prevMid = s.lastMidPriceX64;
    uint64 prevBlock = s.lastUpdateBlock;

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```
