### Title
SwapAllowlistExtension Gates the Router Address Instead of the Originating User, Allowing Any User to Bypass Per-User Swap Access Control via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the user's address. A pool admin who allowlists the router to enable router-mediated swaps inadvertently grants every user on-chain the ability to bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap()`: [1](#0-0) 

`_beforeSwap()` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other swap entry points) calls `pool.swap()` directly, making the router the `msg.sender` of that call: [4](#0-3) 

The result is a forced binary choice for any pool admin who deploys `SwapAllowlistExtension`:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Router-mediated swaps revert for **everyone**, including allowlisted users |
| Allowlist the router | **Every** user can bypass the per-user allowlist by routing through the router |

There is no configuration that simultaneously enforces per-user access control and permits router-mediated swaps.

---

### Impact Explanation

Any user not on the allowlist can execute swaps on a restricted pool by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) as long as the router address is in `allowedSwapper[pool][router]`. The allowlist guard — the sole mechanism for restricting who may trade against the pool — is completely neutralised for all router-mediated paths. Trades that should have been blocked execute at live oracle prices, draining LP liquidity and protocol fees in favour of non-permitted counterparties.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the canonical periphery swap entry point. Any pool operator who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router address. This is a natural, expected operational step, not an exotic misconfiguration. Once the router is allowlisted, the bypass is available to every address on-chain with no further preconditions.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the **originating user**, not the immediate caller of `pool.swap()`. Two complementary fixes:

1. **Extension-side:** `SwapAllowlistExtension.beforeSwap` should check `recipient` (the address that receives swap output and is set by the router to the user-supplied `params.recipient`) rather than `sender`, or the pool should propagate an `originator` field through the hook interface.

2. **Router-side:** `MetricOmmSimpleRouter` should pass `msg.sender` (the originating user) as the `recipient` only when the user intends to receive output directly, and the extension interface should be extended with an explicit `originator` parameter so guards can always identify the economic actor regardless of routing depth.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]   = true   // alice is permitted
  allowedSwapper[pool][router]  = true   // router allowlisted so alice can use it
  allowedSwapper[pool][attacker] = false  // attacker is NOT permitted

Direct swap by attacker:
  attacker calls pool.swap(...)
  → beforeSwap receives sender = attacker
  → allowedSwapper[pool][attacker] == false → revert NotAllowedToSwap ✓

Router-mediated swap by attacker:
  attacker calls router.exactInputSingle({pool: pool, recipient: attacker, ...})
  → router calls pool.swap(recipient=attacker, ...)
  → beforeSwap receives sender = router
  → allowedSwapper[pool][router] == true → PASSES ✗
  → swap executes, attacker receives output tokens
```

The attacker successfully trades against a pool they are explicitly excluded from, bypassing the `SwapAllowlistExtension` guard entirely.

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
