### Title
SwapAllowlistExtension gates the immediate pool caller (router address) instead of the original swapper, enabling full allowlist bypass via MetricOmmSimpleRouter — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` intermediates a swap, `msg.sender` inside the pool is the router contract, not the original user. If the pool admin allowlists the router address (the natural action to enable router-based swaps for legitimate users), every non-allowlisted user can bypass the gate by routing through the router.

---

### Finding Description

**Actor identity mismatch in `SwapAllowlistExtension.beforeSwap`**

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct key) and `sender` is the first argument passed by the pool. The pool always sets that argument to its own `msg.sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← sender forwarded to extension
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` then encodes that value as the `sender` argument to the extension: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the router contract, not the original EOA:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [4](#0-3) 

The router stores the original `msg.sender` only in transient storage for the payment callback; it is never forwarded to the pool or the extension as the swapper identity. Therefore the extension evaluates `allowedSwapper[pool][router]` rather than `allowedSwapper[pool][original_user]`.

**Contrast with `DepositAllowlistExtension`**: the deposit guard checks `owner`, which is an explicit argument that the liquidity adder passes through unchanged as the actual position owner, so it correctly identifies the economic actor regardless of the intermediary. [5](#0-4) 

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and wants to allow allowlisted users to trade through the router must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **any** address — including addresses the admin explicitly never allowlisted — can call `router.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the extension will pass because it sees `sender = router`. The allowlist is completely ineffective for router-mediated swaps. This constitutes a direct bypass of a core pool protection with fund-impacting consequences: non-permitted actors can drain LP value from a pool that was designed to be restricted.

---

### Likelihood Explanation

The scenario requires the pool admin to allowlist the router. This is the natural, expected action for any operator who wants their allowlisted users to be able to use the standard periphery. The router is a first-party, factory-registered contract; allowlisting it is not a misconfiguration in isolation — it is the only way to make the router work on an allowlisted pool. The bypass is therefore reachable on any production curated pool that supports router access.

---

### Recommendation

The extension must gate the original user, not the immediate pool caller. Two viable approaches:

1. **Router injects original caller into `extensionData`**: `MetricOmmSimpleRouter` encodes `msg.sender` into the `extensionData` bytes it forwards to the pool. `SwapAllowlistExtension` decodes and verifies that address. This requires a coordinated change in both the router and the extension.

2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is often the original user, but this breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Separate allowlist entries for direct vs. router paths**: Document that allowlisting the router opens the gate to all users, and provide a separate `allowedRouter` mapping that the extension checks only when `sender` is a known router, then falls back to checking the `recipient` or a user-supplied identity in `extensionData`.

The cleanest fix is option 1: the router appends `abi.encode(msg.sender)` to `extensionData` before forwarding, and the extension decodes it when `sender` is a known router address.

---

### Proof of Concept

```
Setup:
  pool = deploy MetricOmmPool with SwapAllowlistExtension
  admin calls swapExtension.setAllowedToSwap(pool, alice, true)
  admin calls swapExtension.setAllowedToSwap(pool, router, true)
    // ← necessary so alice can use the router

Attack (executed by bob, who is NOT allowlisted):
  bob calls router.exactInputSingle({
      pool: pool,
      recipient: bob,
      zeroForOne: true,
      amountIn: X,
      ...
  })

Execution trace:
  router.exactInputSingle (msg.sender = bob)
    → pool.swap(recipient=bob, ...) [msg.sender in pool = router]
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ← PASSES
      → swap executes, bob receives tokens

Result:
  bob swaps successfully on a pool that was supposed to be restricted to alice only.
  The allowlist is completely bypassed.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

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

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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
