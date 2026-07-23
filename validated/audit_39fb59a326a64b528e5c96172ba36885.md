### Title
SwapAllowlistExtension Bypass via MetricOmmSimpleRouter — Any User Can Swap on Curated Allowlisted Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument the pool passes, which is the pool's own `msg.sender`. When swaps are routed through the public `MetricOmmSimpleRouter`, the `sender` the extension sees is the **router's address**, not the actual user. If the pool admin allowlists the router (the only way to make router-mediated swaps work on a curated pool), any unprivileged user can bypass the allowlist entirely by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument forwarded by the pool.

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as `sender`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← pool's msg.sender, i.e. whoever called pool.swap()
  recipient,
  ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The pool's `msg.sender` is now the **router**, so the extension receives `sender = router`. The check becomes:

```
allowedSwapper[pool][router]
```

For any allowlisted user to swap through the router, the admin must allowlist the router address. Once the router is allowlisted, the check passes for **every** caller of the router, regardless of who they are, because the router is a public, permissionless contract.

The `ExtensionCalling._beforeSwap` dispatcher faithfully forwards `msg.sender` as `sender` with no mechanism to carry the original user identity through the router hop:

```solidity
// metric-core/contracts/ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, zeroForOne, amountSpecified, priceLimitX64,
     packedSlot0Initial, bidPriceX64, askPriceX64, extensionData)
  )
);
```

The pool admin faces an inescapable dilemma:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router; core periphery path broken |
| **Allowlist the router** | Any user bypasses the allowlist via the public router |

---

### Impact Explanation

**High — direct policy bypass on curated pools with fund-impacting consequences.**

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a specific set of addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted market makers). Once the router is allowlisted to support normal periphery usage, any unprivileged address can trade on the pool by calling the public router. This breaks the core invariant that the allowlist enforces: "only approved addresses may swap." Unauthorized swaps drain LP assets at oracle-derived prices, constituting a direct loss of LP-owed value and a broken core pool functionality.

---

### Likelihood Explanation

**High.** The bypass requires no special privileges, no flash loans, and no complex setup. Any user who can call `MetricOmmSimpleRouter.exactInputSingle` (a public function) with the target pool address can execute the bypass in a single transaction. The router is a standard, publicly deployed periphery contract. The only precondition is that the pool admin has allowlisted the router — which is the expected operational configuration for any pool that intends to support router-mediated swaps.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **original user**, not the intermediary. Two viable approaches:

1. **Extension-data identity forwarding**: Require the router to encode the original `msg.sender` into `extensionData` and have the extension decode and verify it. The extension should reject calls where the decoded identity does not match an allowlisted address.

2. **Direct-only policy documentation + enforcement**: If router-mediated swaps are intentionally excluded from allowlisted pools, the extension should revert when `sender` is a known router address, and the documentation must clearly state that allowlisted pools cannot use the periphery router.

Option 1 is preferred because it preserves router usability while correctly gating the actual user.

---

### Proof of Concept

```
1. Deploy MetricOmmPool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only allowed swapper
3. Pool admin calls setAllowedToSwap(pool, router, true)  // required for alice to use the router
4. bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: pool,
           zeroForOne: true,
           amountIn: X,
           recipient: bob,
           ...
       })
5. Router calls pool.swap(bob, true, X, ...) with msg.sender = router
6. Pool calls _beforeSwap(router, bob, ...)
7. ExtensionCalling encodes sender=router and calls extension.beforeSwap(router, ...)
8. Extension evaluates: allowedSwapper[pool][router] == true  → passes
9. Bob's swap executes on the curated pool despite never being allowlisted.
```

**Corrupted value:** `allowedSwapper[pool][router]` is used as a proxy for the actual user identity check. The extension's per-user gate is reduced to a per-intermediary gate, making the allowlist ineffective against any caller of the public router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
