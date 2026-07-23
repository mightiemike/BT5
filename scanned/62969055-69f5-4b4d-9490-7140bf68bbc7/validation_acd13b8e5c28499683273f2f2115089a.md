### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the actual user. If the pool admin allowlists the router to enable router-based swaps for their curated users, every unprivileged user can bypass the per-user allowlist by calling the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then uses that `sender` as the identity to look up in the per-pool allowlist: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router address**, so `sender` forwarded to the extension is the router, not the actual user. The extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`.

This creates an irreconcilable configuration dilemma for any pool admin who deploys a curated pool with `SwapAllowlistExtension`:

| Admin choice | Consequence |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all — broken core functionality |
| **Allowlist the router** | Every unprivileged user bypasses the per-user allowlist via the router |

The research document's own invariant for this target is explicit: *"the hook must gate the same actor the pool designers thought they were allowlisting"* and *"assert the hook cannot be bypassed by routing through an intermediate public contract."* [5](#0-4) 

`setAllowedToSwap` is keyed by individual swapper address. There is no mechanism in the router to forward the real user identity to the extension; the router stores `msg.sender` only in transient storage for the payment callback, not for extension identity checks. [6](#0-5) 

---

### Impact Explanation

When a pool admin allowlists the router (the natural step to let their curated users trade via the supported periphery), the allowlist is rendered completely ineffective: any address can call `router.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and the extension will pass because `allowedSwapper[pool][router] == true`. The pool's curation policy — which may exist to enforce KYC, institutional access, or risk controls — is silently voided. Unauthorized traders gain full swap access to a pool whose pricing and liquidity were sized for a restricted set of counterparties, directly exposing LP funds to unintended adverse selection.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint for end users. A pool admin who configures `SwapAllowlistExtension` to restrict swaps will inevitably discover that their allowlisted users cannot trade via the router and will allowlist the router to fix it — the exact step that opens the bypass. The mistake is not detectable from the extension's interface or documentation alone.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the **economically responsible actor**, not the immediate `msg.sender` of the pool call. Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: The router encodes the real `msg.sender` into `extensionData`; the extension verifies a signature or trusted-forwarder pattern to recover the actual user.
2. **Check `sender` only when it is not a recognized router**: The extension maintains a registry of trusted routers; when `sender` is a router, it reads the real user identity from a standardized field in `extensionData`.

Either way, the invariant must hold: `allowedSwapper[pool][realUser]` is the predicate that determines access, regardless of which supported periphery contract relays the call.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, alice is the only allowlisted user
swapExtension.setAllowedToSwap(address(pool), alice, true);

// Pool admin allowlists the router so alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Bob (not allowlisted) bypasses the allowlist via the router
vm.startPrank(bob);
token0.approve(address(router), type(uint256).max);
// This succeeds: extension sees sender == router, allowedSwapper[pool][router] == true
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    recipient: bob,
    deadline: block.timestamp,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: true,
    priceLimitX64: 0,
    extensionData: ""
}));
vm.stopPrank();

// Alice swapping directly still works, but bob — who is NOT allowlisted — also succeeded
// The allowlist is fully bypassed for any user who routes through the router
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-29)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
