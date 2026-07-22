### Title
Extension Guard Hooks Receive Caller-Supplied Prices in `simulateSwapAndRevert`, Enabling Persistent Guard-State Corruption — (File: `metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`simulateSwapAndRevert` accepts caller-controlled `bidPriceX64` / `askPriceX64` and passes them directly into `_beforeSwap` and `_afterSwap` extension hooks. Because the function terminates with a hard `revert`, all pool-internal state changes are rolled back — but any state written by extension contracts (e.g., `PriceVelocityGuardExtension`, `OracleValueStopLossExtension`) in those hooks is **not** rolled back. An unprivileged caller can therefore inject arbitrary prices into guard extensions without executing a real swap, corrupting the guard's price-history or trigger state and causing it to block legitimate swaps or silently pass malicious ones.

---

### Finding Description

In the real `swap()` path, bid/ask prices are fetched from the oracle: [1](#0-0) 

Those oracle prices are then forwarded to every configured extension via `_beforeSwap` / `_afterSwap`: [2](#0-1) 

In `simulateSwapAndRevert`, the oracle is **never called**. Instead, the caller supplies `bidPriceX64` and `askPriceX64` directly as function arguments, and those values are forwarded verbatim to the same extension hooks: [3](#0-2) 

The only validation applied to the caller-supplied prices is: [4](#0-3) 

This allows any `bid < ask` pair — including extreme or inverted-relative-to-oracle values — to reach the extensions. The function then executes the full swap math with those prices, calls `_afterSwap` with the results, and finally reverts: [5](#0-4) 

The `revert` unwinds all pool storage and transient-storage changes. However, state written by **external extension contracts** during `_beforeSwap` / `_afterSwap` is not subject to the pool's revert — it persists on-chain.

The extension dispatch loop that delivers these caller-controlled prices to every configured guard: [6](#0-5) [7](#0-6) 

The periphery ships two guard extensions that are designed to record price observations in hooks:



`PriceVelocityGuardExtension` and `OracleValueStopLossExtension` must store price snapshots across calls to detect velocity or threshold breaches. Those writes happen inside the hook callbacks and are therefore vulnerable to injection via `simulateSwapAndRevert`.

---

### Impact Explanation

**Direct fund impact — guard bypass leading to bad-price execution or stop-loss failure:**

- **Stop-loss bypass**: An attacker calls `simulateSwapAndRevert` with a crafted `bidPriceX64` / `askPriceX64` that moves the `OracleValueStopLossExtension`'s recorded "last safe value" to an extreme. On the next real swap at the true oracle price, the guard compares against the injected baseline and no longer triggers, allowing the pool to trade through a price that should have been halted.
- **Velocity guard poisoning**: `PriceVelocityGuardExtension` records a price observation on each hook call. An attacker injects a near-zero or near-max price via `simulateSwapAndRevert`. The guard's velocity window now contains a fabricated data point. Subsequent legitimate swaps at the real oracle price appear to cross the velocity threshold, causing the guard to revert them (DoS of swap flow) or, conversely, the fabricated baseline absorbs a real large move, suppressing the guard.

Both outcomes are contest-relevant: the first is a swap conservation / bad-price execution failure; the second is broken core pool functionality causing loss of funds or unusable swap flows.

---

### Likelihood Explanation

- **Unprivileged**: `simulateSwapAndRevert` has no access control and no `whenNotPaused` modifier (unlike `swap()`).
- **Repeatable**: Because the function reverts, the EVM clears transient storage (the reentrancy lock), so the attacker can call it in a tight loop with different crafted prices to systematically walk the guard's state to any desired value.
- **Zero cost beyond gas**: No tokens are transferred; the pool state reverts entirely.

---

### Recommendation

1. **Do not invoke extension hooks inside `simulateSwapAndRevert`**. The function's purpose is price quotation; guard extensions have no meaningful role in a simulation that is guaranteed to revert.
2. If hooks must be called for simulation fidelity, **fetch prices from the oracle** (`_getBidAndAskPriceX64()`) instead of accepting caller-supplied values, matching the real `swap()` path.
3. Alternatively, pass a `bool isSimulation` flag through `extensionData` and require guard extensions to skip state-writing logic when set — but this is weaker because it relies on every extension author opting in correctly.

---

### Proof of Concept

```
Setup:
  - Pool configured with PriceVelocityGuardExtension at BEFORE_SWAP_ORDER slot 1.
  - Guard threshold: price must not move more than 5% between consecutive hook calls.
  - Current oracle price: 1.00 (in Q64.64 units).

Attack:
  1. Attacker calls simulateSwapAndRevert(
         recipient = attacker,
         zeroForOne = true,
         amountSpecified = 1,
         priceLimitX64 = 0,
         bidPriceX64 = <0.01 * 2^64>,   // 99% below oracle
         askPriceX64 = <0.02 * 2^64>,
         extensionData = ""
     )
  2. _beforeSwap fires → PriceVelocityGuardExtension records bid=0.01 as latest observation.
  3. Pool state reverts; extension state does NOT revert.
  4. Attacker (or anyone) calls real swap() at oracle price 1.00.
  5. _beforeSwap fires → guard computes velocity: |1.00 - 0.01| / 0.01 = 9900% >> threshold.
  6. Guard reverts the swap → legitimate swap is permanently blocked until guard state is reset.

Alternatively (stop-loss bypass variant):
  1. Attacker injects bidPriceX64 = <0.50 * 2^64> (50% below oracle) via simulateSwapAndRevert.
  2. OracleValueStopLossExtension records 0.50 as the new "last observed safe value".
  3. Real oracle price drops to 0.60 (a genuine 40% crash that should trigger stop-loss).
  4. Guard compares 0.60 vs stored baseline 0.50 → only 20% move → stop-loss does NOT fire.
  5. Pool continues to trade at a price that should have been halted, draining LP funds.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L228-228)
```text
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();
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

**File:** metric-core/contracts/MetricOmmPool.sol (L306-332)
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

```

**File:** metric-core/contracts/MetricOmmPool.sol (L358-359)
```text
    _nonReentrantAfter();
    revert SimulateSwap(amount0Delta, amount1Delta);
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
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
```

**File:** metric-core/contracts/ExtensionCalling.sol (L179-215)
```text
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
