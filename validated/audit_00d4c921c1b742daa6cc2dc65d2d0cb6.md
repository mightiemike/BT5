### Title
`SwapAllowlistExtension` gates the router address instead of the economic actor, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool, so the allowlist checks the router's address — not the user's. If the pool admin allowlists the router (required for any allowlisted user to use it), every unprivileged user can bypass the individual-user gate by routing through the same public router contract.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the value forwarded from `MetricOmmPool.swap`:

```solidity
_beforeSwap(
    msg.sender,   // ← pool's msg.sender = direct caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the `sender` argument to every configured extension: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [4](#0-3) 

So `msg.sender` inside `pool.swap()` is the **router contract**, not the originating user. The allowlist therefore evaluates `allowedSwapper[pool][router]` — a single boolean that covers every user who routes through that contract.

The pool admin faces an impossible choice:
- **Do not allowlist the router** → allowlisted users cannot use the router at all.
- **Allowlist the router** → every user, including those explicitly excluded, can bypass the gate by calling any of `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`. [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, whitelisted market makers, or protocol-owned accounts) is fully open to any user who routes through `MetricOmmSimpleRouter`. The attacker pays the pool's input token and receives the output token at oracle-derived prices — a complete, economically meaningful swap — without ever appearing on the allowlist. This breaks the core invariant the extension is designed to enforce and constitutes unauthorized extraction of pool liquidity at oracle prices.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless periphery contract. No special role, token balance, or prior interaction is required. Any user who knows the router address (it is a deployed, documented periphery contract) can call it. The bypass requires a single transaction and zero privileged access.

---

### Recommendation

Pass the **originating user** through the swap path so the allowlist can gate the economic actor rather than the intermediary. Two complementary approaches:

1. **Router-level**: Have the router encode the original `msg.sender` in `extensionData` and have the extension decode and check it. This requires a convention between the router and the extension.

2. **Extension-level**: Change `beforeSwap` to check `recipient` (the address receiving output tokens) instead of `sender` when `sender` is a known router, or require pools using `SwapAllowlistExtension` to be called directly (document that router-mediated swaps are incompatible with this extension).

3. **Preferred**: Add a `trustedForwarder` registry to `SwapAllowlistExtension` so that when `sender` is a registered forwarder, the extension reads the real user from `extensionData` instead.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is allowed
  - Pool admin calls setAllowedToSwap(pool, router, true)  // router must be allowed for alice to use it
  - bob is NOT on the allowlist.

Attack:
  1. bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  2. Router calls pool.swap(bob, ...) — msg.sender to pool = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes; bob receives output tokens at oracle price.

Result:
  bob, who is explicitly excluded from the allowlist, completes a full swap
  against the restricted pool. The allowlist invariant is violated.
```

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-40)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
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
