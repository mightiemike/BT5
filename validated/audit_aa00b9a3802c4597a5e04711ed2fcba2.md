### Title
`SwapAllowlistExtension` Gates Router Address Instead of End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router**, not the end user. If the pool admin allowlists the router address (required for any router-mediated swap to succeed), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain when a user swaps through the router:**

```
user → MetricOmmSimpleRouter.exactInputSingle(params)
     → pool.swap(recipient, zeroForOne, amount, priceLimit, "", extensionData)
           [msg.sender = router]
     → _beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
           checks: allowedSwapper[pool][router]
```

The pool passes `msg.sender` as `sender` to every extension hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then evaluates `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router calls `pool.swap()` directly with no end-user identity forwarded: [4](#0-3) 

**The inescapable dilemma for the pool admin:**

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Router-mediated swaps blocked for *everyone*, including allowlisted users |
| **Allowlist the router** | Every user bypasses the allowlist by routing through the router |

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks non-allowlisted users from using the same router.

Additionally, `SwapAllowlistExtension.beforeSwap` drops the `onlyPool` modifier that the base class declares on the same virtual function: [5](#0-4) 

While this alone does not enable a direct exploit (the extension uses `msg.sender` as the pool key, so a direct call from a non-pool address looks up a different, empty mapping slot), it removes the defensive layer that prevents arbitrary callers from probing the extension.

---

### Impact Explanation

Any user who is **not** in the allowlist can execute swaps on a curated pool by calling `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`. The pool admin's access-control policy is structurally unenforceable whenever the router is a required entry point. Pools designed for KYC'd, institutional, or otherwise restricted participants are fully open to the public through the router path. This breaks the core invariant that the allowlist gates the economically relevant actor.

---

### Likelihood Explanation

The trigger is a normal, publicly documented router call — no special permissions, no flash loans, no callbacks. The only prerequisite is that the pool admin has allowlisted the router, which is the expected operational configuration for any pool that intends to support the standard periphery. The `FullMetricExtensionTest` confirms the allowlist is tested only against the direct `TestCaller` address, not against a router intermediary: [6](#0-5) 

No test covers the router-mediated bypass path.

---

### Recommendation

The extension must check the **end user** identity, not the direct caller of `pool.swap()`. Two viable approaches:

1. **Pass end-user identity in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. This requires a trusted encoding convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: For swap allowlists the recipient is often the economically relevant actor; verify whether this matches the intended policy.

3. **Restore `onlyPool`** on `SwapAllowlistExtension.beforeSwap` to match the base class contract and prevent direct external calls.

---

### Proof of Concept

```solidity
// Pool admin setup:
swapExtension.setAllowedToSwap(pool, address(router), true);
// (required so that any allowlisted user can use the router)

// Attacker (not in allowlist) bypasses the gate:
router.exactInputSingle(ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token0),
    recipient:       attacker,
    deadline:        block.timestamp + 1,
    amountIn:        1000,
    amountOutMinimum: 0,
    zeroForOne:      false,
    priceLimitX64:   type(uint128).max,
    extensionData:   ""
}));
// Succeeds: extension sees sender = router (allowlisted), not attacker (not allowlisted).
// Swap executes; allowlist policy is defeated.
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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L81-88)
```text
  function beforeSwap(address, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    virtual
    onlyPool
    returns (bytes4)
  {
    revert ExtensionNotImplemented();
  }
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
