### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User, Allowing Any Unprivileged User to Bypass the Swap Allowlist — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which is the direct `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `sender` equals the **router's address**, not the user's address. If the pool admin allowlists the router (which is the only way to let allowlisted users use the router), any unprivileged user can bypass the allowlist entirely by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` is the production guard that restricts which addresses may swap on a pool:

```solidity
// SwapAllowlistExtension.sol:31-41
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

Inside this call, `msg.sender` is the pool (the extension is called by the pool) and `sender` is whatever address called `pool.swap()`. The check is therefore `allowedSwapper[pool][caller_of_pool_swap]`.

The pool passes `msg.sender` as `sender` to every extension hook:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    zeroForOne,
    amountSpecified,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle(...)`, the router calls `pool.swap(...)`:

```solidity
// MetricOmmSimpleRouter.sol:72-80
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

So `msg.sender` of `pool.swap()` = **router address**. The extension receives `sender = router`, and the allowlist check becomes `allowedSwapper[pool][router]`.

This creates an impossible dilemma for pool admins:

| Admin configuration | Effect |
|---|---|
| Allowlist the router (`allowedSwapper[pool][router] = true`) | **Any user** can bypass the allowlist by going through the router |
| Do not allowlist the router | Allowlisted users **cannot** use the router at all |

There is no configuration that simultaneously allows allowlisted users to use the router and blocks non-allowlisted users from doing the same.

---

### Impact Explanation

A pool admin who deploys a `SwapAllowlistExtension` to restrict swaps to specific market makers (e.g., to prevent toxic flow, implement a private pool, or enforce KYC) and allowlists the router to let those market makers use the standard periphery entry point inadvertently opens the gate to every user. Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the restricted pool and the allowlist check passes because `sender = router` is allowlisted. This allows unauthorized swaps on a pool whose LP depositors expected restricted access, exposing LP funds to adverse selection or unauthorized trading that the allowlist was designed to prevent.

---

### Likelihood Explanation

Medium. The bypass requires the pool admin to have allowlisted the router, which is the natural and necessary step for any allowlisted user who wants to use the standard periphery. Any user who observes the allowlist configuration on-chain can immediately exploit it by calling the router with the restricted pool address. No special privileges, flash loans, or multi-step setup are required.

---

### Recommendation

The extension must check the **actual end-user's identity**, not the intermediary router's address. The cleanest fix is to have the router encode the originating user's address into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. Alternatively, the pool's `beforeSwap` hook could accept a separate `originator` field, or the extension could fall back to checking `recipient` when `sender` is a registered router. Using `tx.origin` is not recommended due to its own security implications.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists Alice: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin allowlists the router so Alice can use it: `setAllowedToSwap(pool, router, true)`.
4. Bob (not allowlisted) calls `router.exactInputSingle({pool: restrictedPool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, ...)` — `msg.sender` of `pool.swap()` = router.
6. Pool calls `_beforeSwap(sender=router, ...)` → extension receives `sender = router`.
7. Extension checks `allowedSwapper[pool][router] = true` → **no revert**.
8. Bob's swap executes on the restricted pool, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
