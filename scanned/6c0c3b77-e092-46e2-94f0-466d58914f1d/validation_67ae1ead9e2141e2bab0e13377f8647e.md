### Title
`simulateSwapAndRevert` Bypasses `whenNotPaused` and Feeds Caller-Supplied Prices to Extension Hooks Whose State Persists After Revert — (File: `metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.simulateSwapAndRevert` lacks the `whenNotPaused` modifier present on `swap`, and accepts caller-supplied `bidPriceX64`/`askPriceX64` instead of fetching them from the oracle. It still invokes the full `_beforeSwap` / `_afterSwap` extension hook chain. Because extensions are separate contracts, their storage writes survive the function's terminal `revert SimulateSwap(…)`. Any caller can therefore inject arbitrary prices into extension state (e.g. `PriceVelocityGuardExtension`, `OracleValueStopLossExtension`) at any time — including while the pool is paused — without executing a real swap or paying any token cost.

---

### Finding Description

`swap()` is guarded:

```solidity
// MetricOmmPool.sol line 224
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) ...
```

`simulateSwapAndRevert()` is not:

```solidity
// MetricOmmPool.sol line 314
function simulateSwapAndRevert(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint128 bidPriceX64,   // ← caller-supplied, not from oracle
    uint128 askPriceX64,   // ← caller-supplied, not from oracle
    bytes calldata extensionData
) external nonReentrant(PoolActions.SIMULATE_SWAP_AND_REVERT) ...
```

It then calls the full hook chain with those attacker-controlled prices:

```solidity
// MetricOmmPool.sol lines 321-331
_beforeSwap(
    msg.sender, recipient, zeroForOne, amountSpecified,
    priceLimitX64, packedSlot0Initial,
    bidPriceX64, askPriceX64,   // ← attacker values
    extensionData
);
```

and after executing the swap math:

```solidity
// MetricOmmPool.sol lines 342-356
_afterSwap(
    msg.sender, recipient, zeroForOne, amountSpecified,
    priceLimitX64, packedSlot0Initial, packedSlot0Final,
    bidPriceX64, askPriceX64,   // ← attacker values
    amount0Delta.toInt128(), amount1Delta.toInt128(),
    protocolFeeAmount, extensionData
);
```

`ExtensionCalling._callExtensionsInOrder` dispatches each hook via `CallExtension.callExtension`, which is an external `call` to a separate contract:

```solidity
// ExtensionCalling.sol lines 75-86
function _callExtensionsInOrder(uint256 order, bytes memory data) private {
    ...
    CallExtension.callExtension(extension, data);   // external call
    ...
}
```

EVM semantics: when `simulateSwapAndRevert` reverts at line 359 (`revert SimulateSwap(…)`), only the pool's own storage and transient storage are rolled back. The extension contracts' storage writes — price observations, velocity snapshots, stop-loss state — are **not** rolled back. The attacker pays only gas.

The `whenNotPaused` gap compounds this: even when the pool admin has paused the pool in response to an oracle anomaly or emergency (exactly the scenario the pause mechanism is designed for), `simulateSwapAndRevert` remains callable, so the attacker can keep injecting fake prices into extension state throughout the pause window.

---

### Impact Explanation

A `PriceVelocityGuardExtension` or `OracleValueStopLossExtension` that records bid/ask observations in `afterSwap` will accumulate attacker-supplied price history. Depending on the guard's logic:

- **Bad-price execution**: the guard's velocity window is pre-seeded with a fabricated price trajectory that makes the real oracle price appear "within normal range," causing the guard to pass a subsequent real swap that it should have blocked. LPs receive less than the oracle-fair amount — direct loss of LP principal.
- **Inverted guard state**: the guard is tricked into believing a stop-loss condition has already been observed, altering its threshold comparison for future swaps.

Both outcomes match the allowed impact gate: *bad-price execution* and *direct loss of LP assets*.

---

### Likelihood Explanation

- `simulateSwapAndRevert` is `external` with no access control and no `whenNotPaused`.
- The attacker needs only to call it with crafted `bidPriceX64`/`askPriceX64` values; no tokens are transferred (the function always reverts before settlement).
- The attack is repeatable at negligible cost (gas only) and can be automated to continuously corrupt extension state.
- It is especially easy to execute during a pause window, when the admin's attention is on the oracle incident and the pool's swap path is already blocked.

---

### Recommendation

1. Add `whenNotPaused` to `simulateSwapAndRevert` so that the same pause gate that protects `swap` also protects the hook chain from being driven with arbitrary prices.
2. Either skip extension hook calls entirely inside `simulateSwapAndRevert` (since simulation does not need to mutate extension state), or pass a flag to extensions indicating the call originates from a simulation context so they can skip storage writes.
3. Document clearly that any extension whose `beforeSwap`/`afterSwap` implementation writes to its own storage must treat simulation calls as read-only.

---

### Proof of Concept

```
1. Pool is configured with PriceVelocityGuardExtension in afterSwap order.
2. Admin pauses the pool (pauseLevel = 1) due to an oracle anomaly.
3. Attacker calls:
       pool.simulateSwapAndRevert(
           attacker,
           true,
           1,
           0,
           fabricatedBidX64,   // e.g. 50% below real oracle price
           fabricatedAskX64,
           ""
       );
   The call reverts with SimulateSwap, but PriceVelocityGuardExtension.afterSwap
   has already written fabricatedBidX64/fabricatedAskX64 into its observation ring.
4. Attacker repeats step 3 N times to fill the velocity window with fake history.
5. Admin unpauses the pool.
6. Real oracle price is now within the fabricated "normal" band recorded by the guard.
7. Attacker executes a real swap at the stale/manipulated effective price;
   the velocity guard passes it. LPs receive less than oracle-fair output.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L341-360)
```text
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

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
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
