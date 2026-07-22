### Title
`simulateSwapAndRevert` Passes Caller-Controlled Prices to Extension Hooks, Allowing Persistent Guard-State Corruption — (`metric-core/contracts/MetricOmmPool.sol`, `metric-periphery/contracts/extensions/`)

---

### Summary

`MetricOmmPool.simulateSwapAndRevert` accepts caller-supplied `bidPriceX64` / `askPriceX64` and forwards them to the full `_beforeSwap` / `_afterSwap` extension hook pipeline. Because extensions are separate contracts, any state they write during those callbacks persists even after the pool reverts. The real `swap()` path fetches prices from the oracle; `simulateSwapAndRevert` does not. Any unprivileged caller can therefore feed arbitrary prices into guard extensions (`PriceVelocityGuardExtension`, `OracleValueStopLossExtension`) to corrupt their stored state without executing a real swap.

---

### Finding Description

**Real `swap()` — oracle-sourced prices:**

```solidity
// MetricOmmPool.sol ~L228
(uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();
_beforeSwap(msg.sender, recipient, zeroForOne, amountSpecified,
            priceLimitX64, packedSlot0Initial,
            bidPriceX64, askPriceX64, extensionData);
``` [1](#0-0) 

**`simulateSwapAndRevert` — caller-supplied prices, same hook pipeline:**

```solidity
// MetricOmmPool.sol ~L306-L330
function simulateSwapAndRevert(
    address recipient, bool zeroForOne, int128 amountSpecified,
    uint128 priceLimitX64,
    uint128 bidPriceX64,   // ← caller-controlled
    uint128 askPriceX64,   // ← caller-controlled
    bytes calldata extensionData
) external nonReentrant(PoolActions.SIMULATE_SWAP_AND_REVERT) ...
``` [2](#0-1) 

The only validation applied to the caller-supplied prices is:

```solidity
if (bidPriceX64 >= askPriceX64) revert BidGreaterThanAsk();
if (bidPriceX64 == 0) revert BidIsZero();
``` [3](#0-2) 

These prices are then forwarded verbatim into `_beforeSwap` and `_afterSwap`: [4](#0-3) 

`_beforeSwap` / `_afterSwap` call every configured extension in order: [5](#0-4) 

Extensions are separate deployed contracts. When the pool reaches `revert SimulateSwap(...)` at the end of `simulateSwapAndRevert`, the pool's own storage is rolled back, but **any storage written by an extension contract during the hook call is not rolled back** — it is a cross-contract call whose effects are already committed to the extension's own storage slot.

The production guard extensions are:



`PriceVelocityGuardExtension` must store a "last observed price" (and timestamp) to compute velocity across calls. `OracleValueStopLossExtension` must store a "halted" flag or a reference price to decide whether to block swaps. Both are updated inside `beforeSwap` / `afterSwap` callbacks using the `bidPriceX64` / `askPriceX64` values passed by the pool — which in the `simulateSwapAndRevert` path are attacker-controlled.

Additionally, `simulateSwapAndRevert` lacks the `whenNotPaused` modifier present on `swap()`, so it can be called even when the pool is paused (e.g., after a stop-loss has halted the pool). [6](#0-5) 

---

### Impact Explanation

**Velocity guard corruption → bad-price swap execution:**
An attacker calls `simulateSwapAndRevert` with an extreme `bidPriceX64` (e.g., 1 or `type(uint128).max - 1`). `PriceVelocityGuardExtension.beforeSwap` stores this as the "last price." On the next real `swap()`, the velocity calculation compares the oracle price against the corrupted stored price. If the stored price is far below the current oracle price, the computed velocity is enormous, triggering a false halt and DoS-ing legitimate swaps. If the stored price is far above the current oracle price, the velocity appears negative or zero, bypassing the guard and allowing a real swap to proceed at a moment the guard was designed to block — directly enabling bad-price execution against LPs.

**Stop-loss reset → swaps execute through a halted pool:**
If `OracleValueStopLossExtension` stores a "halted" boolean and resets it when prices recover, an attacker can call `simulateSwapAndRevert` with a price above the recovery threshold. The extension writes `halted = false` to its own storage. The pool reverts, but the extension's halt flag is now cleared. Real swaps can now proceed through a pool that should remain halted, causing LP principal loss.

Both paths satisfy the allowed impact gate: bad-price execution and broken core swap functionality causing loss of LP assets.

---

### Likelihood Explanation

- `simulateSwapAndRevert` is `external` with no access control and no `whenNotPaused` guard.
- The only price constraint is `0 < bid < ask`, trivially satisfied with any two consecutive integers.
- No special role, token, or setup is required.
- The attacker pays only gas.

---

### Recommendation

1. **Do not call extension hooks inside `simulateSwapAndRevert`**, or introduce a simulation-mode flag that extensions can detect and skip state-mutating logic.
2. Alternatively, validate that caller-supplied `bidPriceX64` / `askPriceX64` are within a tight band of the current oracle price before forwarding them to extensions.
3. Add `whenNotPaused` to `simulateSwapAndRevert` so a halted pool cannot be manipulated via the simulation path.
4. Guard extensions should check `msg.sender == boundPool` **and** that the call originates from a non-simulation context, or maintain a separate read-only view of their state for simulation queries.

---

### Proof of Concept

```
1. Pool P is deployed with PriceVelocityGuardExtension E configured on beforeSwap.
   E stores lastPriceX64 = oracle_price (e.g., 2^64 ≈ 1.0 in Q64).

2. Attacker calls:
   P.simulateSwapAndRevert(
       attacker,          // recipient
       false,             // zeroForOne
       1,                 // amountSpecified (tiny)
       type(uint128).max, // priceLimitX64
       1,                 // bidPriceX64  ← manipulated (near zero)
       2,                 // askPriceX64  ← manipulated (near zero)
       ""
   );

3. Pool calls E.beforeSwap(..., bidPriceX64=1, askPriceX64=2, ...).
   E writes lastPriceX64 = 1 to its own storage.
   Pool reverts with SimulateSwap — pool storage is rolled back.
   E.lastPriceX64 = 1 persists.

4. Legitimate LP calls P.swap(...) at the real oracle price (2^64).
   E.beforeSwap computes velocity = (2^64 - 1) / elapsed_seconds → enormous.
   Guard triggers → swap reverts with "velocity exceeded."
   Pool is effectively DoS-ed for all swaps until lastPriceX64 is reset.

   OR (inverse): attacker sets lastPriceX64 = type(uint128).max - 1.
   Next real swap: velocity = (2^64 - (type(uint128).max-1)) → negative/zero.
   Guard sees no velocity → allows swap at a moment it should have blocked.
   LPs receive less than oracle-fair value.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L228-240)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-core/contracts/MetricOmmPool.sol (L306-360)
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
    if (bidPriceX64 == 0) revert BidIsZero();

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
  }
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-215)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }

  function _afterSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint256 packedSlot0Final,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    int128 amount0Delta,
    int128 amount1Delta,
    uint256 protocolFeeAmount,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      AFTER_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.afterSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          packedSlot0Final,
          bidPriceX64,
          askPriceX64,
          amount0Delta,
          amount1Delta,
          protocolFeeAmount,
          extensionData
        )
      )
    );
  }
```
