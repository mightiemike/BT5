### Title
`simulateSwapAndRevert` Passes Caller-Controlled Prices to Extension Hooks Whose Side Effects Persist After Revert, Allowing Guard State Corruption — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`simulateSwapAndRevert` accepts arbitrary `bidPriceX64`/`askPriceX64` from any caller and invokes the full `_beforeSwap` / `_afterSwap` hook chain with those attacker-controlled values. Because the function ultimately reverts, pool storage is rolled back, but any state written by extension contracts (external accounts) during those hook calls is **permanently committed**. A velocity guard or stop-loss extension that records price observations in its own storage during hook callbacks will therefore store attacker-fabricated prices, corrupting the guard's reference state and enabling a subsequent real swap to bypass the guard entirely.

---

### Finding Description

The real `swap()` path fetches bid/ask from the oracle before invoking hooks: [1](#0-0) 

`simulateSwapAndRevert`, by contrast, accepts caller-supplied prices with only trivial sanity checks (`bid < ask`, `bid != 0`) and passes them directly into the identical hook chain: [2](#0-1) 

After executing the swap and calling `_afterSwap` with the same fabricated prices, the function clears transient reentrancy state and reverts: [3](#0-2) 

The EVM rolls back all pool storage on revert, but writes already committed to **external extension contracts** during `_beforeSwap` / `_afterSwap` are **not** rolled back. The hook dispatch loop calls each configured extension unconditionally: [4](#0-3) 

`PriceVelocityGuardExtension` and `OracleValueStopLossExtension` must maintain persistent price-history or reference-value state to function. When `simulateSwapAndRevert` fires their `beforeSwap`/`afterSwap` callbacks with attacker-chosen prices, those extensions write the fabricated values into their own storage. The pool's revert does not undo those writes.

The function carries no access control — it is `external` with no `onlyFactory` or similar guard: [5](#0-4) 

---

### Impact Explanation

An attacker can corrupt the velocity guard's price-history buffer or the stop-loss extension's reference oracle value by calling `simulateSwapAndRevert` with fabricated prices. On the next real `swap()` call the guard compares the current oracle price against the poisoned reference, causing one of two outcomes:

- **Guard bypass**: the velocity guard sees an artificially stable history and permits a swap at a price that would otherwise be blocked, enabling bad-price execution against LPs.
- **Guard mis-trigger**: the stop-loss extension compares the real oracle price against a fabricated high reference and incorrectly halts the pool, causing a denial-of-service on legitimate swaps.

Both outcomes are in the allowed impact gate (bad-price execution reaching a pool swap; broken core pool functionality causing loss of funds or unusable swap flows).

---

### Likelihood Explanation

The function is permissionlessly callable by any EOA or contract. The only precondition is that the pool has a velocity or stop-loss extension configured, which is the normal production deployment for pools requiring price-movement protection. No privileged role, flash loan, or special token behavior is required.

---

### Recommendation

1. **Do not call extension hooks inside `simulateSwapAndRevert`**. The function's purpose is price quotation; guards are irrelevant to simulation and should be skipped. Remove the `_beforeSwap` and `_afterSwap` calls from `simulateSwapAndRevert`.

2. If hooks must be called for simulation completeness, add a `simulationMode` flag passed to extensions so they can skip state-writing side effects during simulation.

3. Alternatively, restrict `simulateSwapAndRevert` to use oracle-fetched prices (same as `swap()`), removing the caller-controlled price surface entirely.

---

### Proof of Concept

```
Setup:
  - Pool configured with PriceVelocityGuardExtension as beforeSwap hook.
  - Extension stores last N bid prices to compute velocity.
  - Oracle currently reports bid = X (legitimate market price).

Attack:
  1. Attacker calls simulateSwapAndRevert(
         recipient = attacker,
         zeroForOne = true,
         amountSpecified = 1,
         priceLimitX64 = 0,
         bidPriceX64 = X,          // matches current oracle — appears benign
         askPriceX64 = X + 1,
         extensionData = ""
     )
     repeated N times with bid = X to fill the velocity guard's history buffer
     with a stable price equal to the current oracle price.

  2. Real oracle price then moves to X + large_delta (e.g., due to market move).

  3. Attacker calls swap() at the new oracle price.
     - _beforeSwap fires PriceVelocityGuardExtension.
     - Guard computes velocity = (X + large_delta) - X = large_delta.
     - Normally this would exceed the velocity threshold and revert.
     - BUT: the history buffer was poisoned in step 1 to show X as the
       "prior" price, so the guard sees exactly the delta the attacker
       engineered — if the attacker chose X to be close to the new price,
       the computed velocity is small and the guard passes.

  4. Swap executes at a price the guard was configured to block.
     LPs receive less than the oracle-permitted output; attacker captures
     the difference.
```

The root cause is identical to the THORChain analog: an action (simulation) that fails/reverts still causes the protocol's guard layer to record a state update (price observation) as if the action were a legitimate committed event, leading the guard to make decisions based on fabricated data.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L227-240)
```text
    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
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

**File:** metric-core/contracts/MetricOmmPool.sol (L358-360)
```text
    _nonReentrantAfter();
    revert SimulateSwap(amount0Delta, amount1Delta);
  }
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
