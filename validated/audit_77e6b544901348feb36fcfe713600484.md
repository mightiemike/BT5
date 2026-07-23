### Title
`simulateSwapAndRevert` Commits Extension Guard State with Caller-Supplied Prices Before Reverting, Permanently Corrupting Guard Observations - (File: `metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.simulateSwapAndRevert` invokes `_beforeSwap` and `_afterSwap` extension hooks with **caller-supplied** `bidPriceX64`/`askPriceX64` values, then reverts the pool's own storage. Because extensions (`PriceVelocityGuardExtension`, `OracleValueStopLossExtension`) are **external contracts**, their state changes are committed and cannot be rolled back by the pool's revert. Any unprivileged caller can inject arbitrary price observations into guard extensions without executing a real swap, permanently corrupting guard state and enabling guard bypass or permanent swap DoS.

---

### Finding Description

`MetricOmmPool.simulateSwapAndRevert` is a public, unrestricted function:

```solidity
function simulateSwapAndRevert(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint128 bidPriceX64,   // ← caller-supplied, not fetched from oracle
    uint128 askPriceX64,   // ← caller-supplied, not fetched from oracle
    bytes calldata extensionData
) external nonReentrant(PoolActions.SIMULATE_SWAP_AND_REVERT) returns (int128, int128) {
``` [1](#0-0) 

The function proceeds to call `_beforeSwap` and `_afterSwap` with those caller-supplied prices:

```solidity
_beforeSwap(
    msg.sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,   // ← attacker-controlled
    askPriceX64,   // ← attacker-controlled
    extensionData
);
// ... swap execution ...
_afterSwap(..., bidPriceX64, askPriceX64, ...);

_nonReentrantAfter();
revert SimulateSwap(amount0Delta, amount1Delta);
``` [2](#0-1) 

`_beforeSwap` and `_afterSwap` dispatch to all configured extensions via `_callExtensionsInOrder`, which issues **external `call`s** to each extension contract: [3](#0-2) 

The critical invariant break: when the pool executes `revert SimulateSwap(...)`, it rolls back **only the pool's own storage**. The external calls to `PriceVelocityGuardExtension` and `OracleValueStopLossExtension` have already been committed to those contracts' storage. EVM semantics guarantee that a reverting caller cannot undo state changes already written by a completed external call.

The real `swap` function, by contrast, fetches prices from the oracle and cannot be called with fabricated values:

```solidity
(uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64(); // oracle-enforced
``` [4](#0-3) 

`simulateSwapAndRevert` skips the oracle fetch entirely and accepts prices as parameters, bypassing the oracle guard that protects real swaps.

The `onlyPool` modifier in `BaseMetricExtension` only checks that the caller is *a* factory pool — it does not verify which pool or whether the call originates from a real swap context:

```solidity
modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
        revert OnlyPool(msg.sender, FACTORY);
    }
    _;
}
``` [5](#0-4) 

So the extension accepts the call from the pool during simulation as legitimate, records the attacker-supplied price observation, and the pool's subsequent revert cannot undo it.

**Analog to the external bug:** In the BabelToken report, a disabled receiver has tokens allocated to it but no mechanism forces recovery — the only actor who could trigger recovery (the receiver) has no incentive. Here, the extension guard has its state corrupted by `simulateSwapAndRevert` but no mechanism can undo it — the only actor who could fix it (the extension admin) has no automatic recovery path, and the corruption is invisible until a real swap triggers the guard incorrectly.

---

### Impact Explanation

**Scenario A — Guard Bypass (High):** An attacker calls `simulateSwapAndRevert` with fabricated "safe" prices just before a real adverse price move. `OracleValueStopLossExtension` records the fake safe observation as the baseline. When the real swap occurs at the adversely moved price, the guard compares against the fake baseline and does not trigger. The stop-loss that should have blocked the bad-price swap is bypassed, allowing traders to drain LP assets at stale/inverted prices. This matches the allowed impact: *"Bad-price execution: stale, inverted, unbounded, or unclamped bid/ask quote reaches a pool swap."*

**Scenario B — Permanent DoS (Medium-High):** An attacker calls `simulateSwapAndRevert` with extreme fabricated prices (e.g., `bidPriceX64 = 1`, `askPriceX64 = type(uint128).max`). `PriceVelocityGuardExtension` records this as an extreme velocity event. All subsequent real swaps trigger the velocity guard and revert. LPs cannot swap or withdraw through the pool. This matches: *"Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."*

---

### Likelihood Explanation

- `simulateSwapAndRevert` is `external` with no access control — any EOA or contract can call it.
- No tokens are required; the function always reverts before any transfer settles.
- The attacker only needs to craft a transaction with desired `bidPriceX64`/`askPriceX64` values.
- The attack is repeatable and cheap (gas only).
- The function is designed for off-chain simulation via `eth_call`, but nothing prevents on-chain execution as a real transaction.

---

### Recommendation

1. **Do not call extension hooks inside `simulateSwapAndRevert`.** The simulation path should skip `_beforeSwap`/`_afterSwap` entirely, or use a flag that extensions can read (e.g., `pool.inSwap()` already exists for this purpose) to skip state-mutating logic during simulation.

2. **Extensions should guard against simulation context.** Guard extensions should check `IMetricOmmPool(msg.sender).inSwap() != address(0)` or an equivalent "real swap" sentinel before writing observations. However, this is a defense-in-depth measure; the root fix belongs in the pool.

3. **Alternatively**, make `simulateSwapAndRevert` use `staticcall` for extension invocations during simulation, reverting if any extension attempts a state write.

---

### Proof of Concept

```
Setup:
  - Pool P configured with PriceVelocityGuardExtension E
  - E records price observations in beforeSwap/afterSwap to compute velocity

Attack (Scenario B — DoS):
  1. Attacker calls:
       P.simulateSwapAndRevert(
           recipient = attacker,
           zeroForOne = true,
           amountSpecified = 1,
           priceLimitX64 = 0,
           bidPriceX64 = 1,           // extreme low
           askPriceX64 = type(uint128).max,  // extreme high
           extensionData = ""
       )
  2. Pool calls E.beforeSwap(..., bidPriceX64=1, askPriceX64=MAX, ...)
     → E records observation: price = 1 (or MAX)
  3. Pool reverts with SimulateSwap — no tokens move, pool storage restored
  4. E's storage retains the fake extreme observation (NOT reverted)

  5. Any subsequent real swap calls E.beforeSwap with the real oracle price
     → E computes velocity = |realPrice - fakeObservation| / timeDelta → enormous
     → E reverts: velocity guard triggered
  6. All swaps on pool P are permanently blocked
     → LPs cannot exit through swaps; pool is effectively bricked
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L228-228)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();
```

**File:** metric-core/contracts/MetricOmmPool.sol (L306-316)
```text
  function simulateSwapAndRevert(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.SIMULATE_SWAP_AND_REVERT) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());
    if (bidPriceX64 >= askPriceX64) revert BidGreaterThanAsk();
```

**File:** metric-core/contracts/MetricOmmPool.sol (L319-359)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();

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

    (uint256 midPriceX64, uint256 baseFeeX64) = SwapMath.midAndSpreadFeeX64FromBidAsk(bidPriceX64, askPriceX64);

    SwapMath.InternalSwapParams memory params =
      SwapMath.InternalSwapParams({midPriceX64: midPriceX64, baseFeeX64: baseFeeX64, priceLimitX64: priceLimitX64});

    (int256 amount0Delta, int256 amount1Delta, uint256 protocolFeeAmount) =
      _executeSwap(zeroForOne, amountSpecified, params);

    uint256 packedSlot0Final = Slot0Library.loadPackedSlot0();
    _afterSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      packedSlot0Final,
      bidPriceX64,
      askPriceX64,
      amount0Delta.toInt128(),
      amount1Delta.toInt128(),
      protocolFeeAmount,
      extensionData
    );

    _nonReentrantAfter();
    revert SimulateSwap(amount0Delta, amount1Delta);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L75-86)
```text
  function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    if (order == 0) return;

    while (true) {
      uint256 extensionIndex = order & 0x7;
      if (extensionIndex == 0) break;
      address extension = _extensionAddress(extensionIndex);
      if (extension == address(0)) revert PanicEmptyExtension();
      CallExtension.callExtension(extension, data);
      order >>= 3;
    }
  }
```

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L19-24)
```text
  modifier onlyPool() {
    if (!IMetricOmmPoolFactory(FACTORY).isPool(msg.sender)) {
      revert OnlyPool(msg.sender, FACTORY);
    }
    _;
  }
```
