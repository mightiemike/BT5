### Title
SwapAllowlistExtension Checks Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass Through the Router Path — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. If the pool admin allowlists the router (the natural step to let allowlisted users use the standard periphery), every unprivileged user can bypass the individual allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to every extension hook: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted for the calling pool (`msg.sender` = pool): [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`: [3](#0-2) 

The same pattern holds for `exactInput` (multi-hop) and `exactOutput`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actual_user]`. This forces the pool admin into an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot use the standard periphery at all (broken core flow) |
| **Allowlist the router** | Every user — allowlisted or not — can bypass the individual allowlist by routing through the router |

Neither option achieves the intended invariant: "only allowlisted users can swap, and they can use the router."

---

### Impact Explanation

Once the pool admin allowlists the router (the expected operational step), any unprivileged user can call `router.exactInputSingle()` and trade on a curated pool that was intended to be restricted. The allowlist guard is completely neutralised on the router path. If the allowlist was protecting LP funds from specific counterparties (e.g., a KYC-gated pool, a pool restricted to hedging partners, or a pool guarded against known front-runners), the bypass directly exposes LP principal to the disallowed actors. This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" and "Broken core pool functionality causing loss of funds" impact categories.

---

### Likelihood Explanation

High. The router is the canonical, documented periphery entry point for swaps. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and wants their allowlisted users to be able to use the router will allowlist the router. The bypass is then immediately available to every user with no special setup. The attacker needs only to call `router.exactInputSingle()` targeting the curated pool.

---

### Recommendation

The pool must surface the original user's identity to the extension. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it instead of the raw `sender` argument.
2. **Check `recipient` instead of `sender`**: For single-hop swaps the recipient is typically the user; however this breaks for multi-hop flows where intermediate recipients are the router itself.
3. **Dedicated router forwarding field**: Add an explicit `originator` field to the swap interface that the pool passes to extensions, allowing the router to forward `msg.sender` without ambiguity.

---

### Proof of Concept

```solidity
// Pool admin sets up a curated pool with SwapAllowlistExtension.
// Only `alice` is allowlisted.
extension.setAllowedToSwap(address(pool), alice, true);

// Pool admin also allowlists the router so alice can use the periphery.
extension.setAllowedToSwap(address(pool), address(router), true);

// Now `eve` (not allowlisted) bypasses the guard via the router:
vm.prank(eve);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: eve,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        extensionData: "",
        deadline: block.timestamp + 1
    })
);
// Succeeds: extension saw sender=router, which is allowlisted.
// Eve traded on a pool she was explicitly excluded from.
```

The root cause is at: [5](#0-4) 

where `sender` is the router address, not the actual user, whenever the swap is routed through `MetricOmmSimpleRouter`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
