### Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by the `sender` argument it receives from the pool, which is `msg.sender` of the `pool.swap` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap`, so the extension checks whether the **router** is allowlisted — not the original user. A pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently opens the allowlist to every user who calls the router, defeating the guard entirely.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly, making the router the `msg.sender` of that call: [4](#0-3) 

Therefore the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalUser]`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use the router at all (every router-mediated swap reverts `NotAllowedToSwap`).
- **Allowlist the router** → every user on the network can bypass the allowlist by routing through `MetricOmmSimpleRouter`, because the extension sees only the router address and approves it unconditionally.

The same structural problem applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which call `pool.swap` with `msg.sender = router`. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified market makers, institutional partners, or whitelisted bots) is fully open to any caller once the router is allowlisted. An unauthorized user can:

1. Call `router.exactInputSingle` or `router.exactInput` targeting the restricted pool.
2. The router calls `pool.swap`; the extension sees `sender = router` and passes.
3. The swap executes at the oracle-derived bid/ask price with no further identity check.

This breaks the core invariant that the allowlist gates the economically relevant actor. Unauthorized traders can extract value from LP positions at oracle prices the pool admin intended to expose only to trusted counterparties, causing direct loss of LP principal and fees.

---

### Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` and (b) allowlists the router — a natural and expected configuration for pools that want to support the standard periphery — is immediately vulnerable. No special preconditions, flash loans, or privileged access are required. A single `exactInputSingle` call from any EOA suffices.

---

### Recommendation

The extension must recover the original user identity rather than trusting the `sender` argument, which reflects only the immediate caller of `pool.swap`. Two sound approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`; the extension decodes and verifies it, then checks `allowedSwapper[pool][originalUser]`. The pool must also verify the router is a trusted forwarder to prevent spoofing.

2. **Check both the router and the original user**: Maintain a separate `trustedForwarder` mapping; when `sender` is a trusted forwarder, require the original user address to be passed and verified in `extensionData`.

The simplest safe default is to remove router support from allowlisted pools until the identity-forwarding mechanism is in place, so the allowlist always gates the direct caller.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][user1]  = true   // intended allowed trader
  allowedSwapper[pool][router] = true   // admin adds router so user1 can use it

Attack:
  user2 (not allowlisted) calls:
    router.exactInputSingle({
      pool:      restrictedPool,
      tokenIn:   token0,
      tokenOut:  token1,
      amountIn:  X,
      recipient: user2,
      ...
    })

  router calls pool.swap(recipient=user2, ...) with msg.sender=router
  pool calls extension.beforeSwap(sender=router, ...)
  extension checks allowedSwapper[pool][router] == true  → passes
  swap executes; user2 receives token1 at oracle price

Result:
  user2 bypassed the allowlist entirely.
  user1's exclusive access to the pool is broken.
  LP positions are exposed to unauthorized arbitrage.
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
