### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual End-User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument, which is the pool's `msg.sender` — the router contract — not the actual end-user who initiated the swap. If the router is allowlisted (the natural operational setup), any unprivileged user can bypass the per-user swap allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`:

```solidity
// MetricOmmPool.sol L231
_beforeSwap(
    msg.sender,   // <-- this is the router when called via MetricOmmSimpleRouter
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards this `sender` value directly to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
``` [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks whether this `sender` is allowlisted:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap()`. When a user goes through `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
    );
``` [4](#0-3) 

So the pool sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`. The actual end-user who called the router is never checked. If the pool admin allowlists the router address (the natural operational setup — "allow the official periphery to swap"), every user on the network can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`.

This is structurally identical to the seeded bug: the guard checks the intermediary (`msg.sender` / the approved address / the router) rather than the actual economic actor (`from` / the token owner / the end-user).

Note: `DepositAllowlistExtension` does **not** share this flaw — it ignores `sender` and checks `owner` (the position owner), which is correctly the economic actor for liquidity operations. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted market makers) provides no actual restriction once the router is allowlisted. Any arbitrary address can call `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()`, have the router execute `pool.swap()` on their behalf, and the extension will pass because it sees the allowlisted router address, not the unauthorized caller. The pool's liquidity is exposed to all swappers, directly contradicting the pool admin's access-control intent and potentially enabling unauthorized extraction of LP assets at oracle-anchored prices.

---

### Likelihood Explanation

The likelihood is high. `MetricOmmSimpleRouter` is the primary user-facing swap interface for the protocol. Any pool admin who configures `SwapAllowlistExtension` and also wants users to be able to swap through the official router must allowlist the router — at which point the allowlist is fully bypassed for all users. The bypass requires no special privileges, no malicious setup, and no non-standard tokens: any EOA can call the router.

---

### Recommendation

The `sender` passed to `beforeSwap` must represent the actual end-user, not the intermediary. Two complementary fixes:

1. **Router-side**: `MetricOmmSimpleRouter` should encode the actual `msg.sender` (the end-user) into `extensionData` so that extensions can read the true initiator.

2. **Extension-side**: `SwapAllowlistExtension.beforeSwap()` should decode and check the actual user from `extensionData` when `sender` is a known router/intermediary, or the protocol should define a standard `extensionData` field for the "true initiator" that all periphery contracts populate.

3. **Alternatively**: The pool could expose a separate `swapFor(address user, ...)` entry point that passes `user` as `sender` to extensions, analogous to how `addLiquidity(owner, ...)` correctly separates the payer (`msg.sender`) from the economic actor (`owner`).

---

### Proof of Concept

```
Setup:
  - Pool configured with SwapAllowlistExtension
  - Pool admin calls setAllowedToSwap(pool, router, true)   // allowlist the router
  - Pool admin does NOT call setAllowedToSwap(pool, alice, true)  // alice is NOT allowlisted

Attack:
  1. Alice (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: alice, ...})

  2. Router calls:
       pool.swap(alice, zeroForOne, amount, ...)
       // msg.sender = router (allowlisted)

  3. Pool calls:
       extension.beforeSwap(router, alice, ...)
       // sender = router

  4. Extension checks:
       allowedSwapper[pool][router] == true  → passes

  5. Alice's swap executes successfully despite not being allowlisted.

Direct call (correctly blocked):
  1. Alice calls pool.swap(...) directly
  2. Extension checks allowedSwapper[pool][alice] == false → reverts NotAllowedToSwap
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
