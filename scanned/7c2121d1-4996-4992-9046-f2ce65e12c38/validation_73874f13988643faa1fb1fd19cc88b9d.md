### Title
SwapAllowlistExtension Gates the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument—which is `msg.sender` of the `pool.swap` call—against the per-pool allowlist. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user's. If the pool admin allowlists the router to support router-mediated swaps, every user—including those not individually allowlisted—can bypass the per-user restriction by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle
         → pool.swap(recipient, ...) [msg.sender = router]
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← wrong actor checked
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
_beforeSwap(
    msg.sender,   // ← router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this verbatim to the extension:

```solidity
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, ...)   // sender = router
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check becomes `allowedSwapper[pool][router]`.

The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user—allowlisted or not—can bypass the per-user gate via the router |

The second branch is the bypass: once the router is allowlisted (the only way to support the standard periphery path), the allowlist ceases to gate individual users.

---

### Impact Explanation

**Direct loss of curation policy on curated pools.** The `SwapAllowlistExtension` is the production mechanism for restricting which addresses may trade on a pool. Any non-allowlisted user can bypass it by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput` instead of calling the pool directly. The pool receives and settles the swap normally; the allowlist guard silently passes because it sees the router, not the user. This is a broken core pool functionality: the allowlist invariant—"only approved addresses may swap"—is violated for every router-mediated trade once the router is allowlisted.

---

### Likelihood Explanation

**High.** The router is the standard, documented user-facing entry point. No special knowledge or privilege is required. Any user who observes that direct pool calls are blocked can immediately retry through the router. The bypass is one function call away and requires no on-chain setup.

---

### Recommendation

The extension must check the **economic actor** (the end user), not the technical caller (the router). Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires the router to cooperate, but the router is a trusted periphery contract.

2. **Check `recipient` instead of `sender`**: For swap allowlists, the recipient is often the user. However, this is semantically imprecise for multi-hop routes where intermediate recipients are the router itself.

The cleanest fix is approach 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension.beforeSwap` decodes and checks that address when `sender` is a known router.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension, only alice is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);

// Admin must also allowlist the router to support standard periphery usage
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Bob (not allowlisted) bypasses the allowlist via the router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        recipient: bob,
        amountIn: 1000,
        amountOutMinimum: 0,
        zeroForOne: true,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// Bob's swap succeeds: extension checked allowedSwapper[pool][router] = true
// Bob received token1 despite not being on the allowlist
assertGt(token1.balanceOf(bob), 0);
```

**Root cause line:** [1](#0-0) 

**`sender` is `msg.sender` of `pool.swap`, set to the router when called via periphery:** [2](#0-1) 

**Router calls `pool.swap` with itself as `msg.sender`:** [3](#0-2) 

**`_beforeSwap` forwards `sender` (= router) verbatim to the extension:** [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
