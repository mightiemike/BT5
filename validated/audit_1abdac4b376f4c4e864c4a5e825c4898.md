### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool receives `msg.sender = router`, so the extension checks the router address — not the actual user. A pool admin who allowlists the router to enable router-mediated swaps for their intended users inadvertently opens the gate to every user, completely defeating the allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any router entry point), the router calls `pool.swap(recipient, ...)` directly: [4](#0-3) 

At that point `msg.sender` inside `pool.swap` is the **router contract**, so `sender` delivered to the extension is the router address — not the end user. The allowlist lookup `allowedSwapper[pool][router]` is evaluated, not `allowedSwapper[pool][user]`.

This creates an irreconcilable actor-binding mismatch:

| Scenario | `sender` checked | Result |
|---|---|---|
| Router NOT allowlisted | router address | All router users blocked, even allowlisted ones |
| Router IS allowlisted | router address | All users pass, even non-allowlisted ones |

There is no configuration that allows the pool admin to permit specific users through the router while blocking others.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists the router (the natural step to let their approved users trade via the standard periphery) unknowingly grants every address on-chain the ability to swap. Non-allowlisted users can drain LP value, front-run, or otherwise interact with a pool that was explicitly designed to restrict access. The allowlist — the sole access-control mechanism on the swap path — is rendered inoperative for all router-mediated flows.

This matches the **Allowlist bypass** impact gate: a curated pool's allowlist is bypassed through the supported public router path, causing direct LP exposure to unauthorized counterparties.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool admin who wants their allowlisted users to be able to use the standard router must allowlist the router address. This is a routine operational step, not an exotic misconfiguration. The bypass is therefore reachable on any production pool that uses `SwapAllowlistExtension` alongside the router.

---

### Recommendation

The extension must gate the **economically relevant actor** — the end user — not the intermediary. Two viable approaches:

1. **`extensionData` forwarding**: Have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension` decode and check that address when `sender` is a known router.
2. **Check `recipient`**: If the pool's design intent is that the recipient is the user, check `recipient` instead of `sender`. This requires confirming the pool's recipient semantics match the allowlist intent.
3. **Router-level enforcement**: Require that the router itself enforces the allowlist before calling the pool, removing the dependency on the extension for router flows.

The analogous fix in the external report was to use `data.amount` consistently; here the fix is to use the **actual user identity** consistently across both direct and router-mediated swap paths.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured as a beforeSwap hook.
2. Pool admin calls setAllowedToSwap(pool, alice, true)       // alice is the intended user
3. Pool admin calls setAllowedToSwap(pool, router, true)      // to let alice use the router
4. Bob (not allowlisted) calls:
       MetricOmmSimpleRouter.exactInputSingle({
           pool: curatedPool,
           recipient: bob,
           ...
       })
5. Router calls pool.swap(bob, ...) → msg.sender = router
6. Extension checks allowedSwapper[pool][router] → true
7. Bob's swap executes successfully despite not being allowlisted.
```

The bypass requires only that the router is allowlisted — a condition the pool admin must set to support their own approved users.

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
