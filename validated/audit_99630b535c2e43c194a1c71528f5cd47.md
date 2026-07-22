### Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `sender`, which the pool sets to `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` becomes the **router's address**, not the end user's address. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

At that point `msg.sender` inside the pool is the **router**, so the extension receives `sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

The pool admin faces an impossible choice:

| Admin decision | Effect |
|---|---|
| Do **not** allowlist the router | All router-mediated swaps revert for every user, including allowed ones |
| **Allowlist the router** | Every user — allowed or not — can bypass the per-user gate by routing through the router |

The `DepositAllowlistExtension` does not share this flaw because it checks `owner`, which is the explicit position owner passed through the call chain and is not overwritten by the intermediary: [5](#0-4) 

---

### Impact Explanation

A curated pool that restricts swaps to specific addresses (KYC'd users, institutional partners, whitelisted market makers) is completely bypassed. Any unprivileged user routes through `MetricOmmSimpleRouter` and trades freely. LP assets are exposed to actors the pool admin explicitly excluded. This is a direct loss-of-policy impact on every pool that deploys `SwapAllowlistExtension` and needs router support — which is the standard production path.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing entry point for swaps. Any user who wants to trade on an allowlisted pool will naturally attempt the router path. No special knowledge or privileged access is required; the bypass is a single standard router call. The condition (router allowlisted) is the only operational configuration that makes the pool usable via the router, so it will be set in every real deployment.

---

### Recommendation

The extension must gate the **actual end user**, not the intermediary. Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` for the allowlist extension. The extension decodes and verifies it. This requires the router to be trusted to supply the correct value, which is acceptable given it is a protocol-controlled contract.

2. **Separate `sender` from `payer` in the hook interface**: Add a dedicated `originator` field to `beforeSwap` that the pool populates from a transient-storage context set by the router before calling `pool.swap`. The extension checks `originator` instead of `sender`.

Until fixed, pools that require per-user swap gating must not allowlist the router and must require users to call `pool.swap` directly.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as extension1, BEFORE_SWAP_ORDER = extension1.
2. Pool admin calls swapExtension.setAllowedToSwap(pool, alice, true).
3. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
   (required so alice can use the router).
4. bob (not allowlisted) calls:
     MetricOmmSimpleRouter.exactInputSingle({
       pool: pool,
       recipient: bob,
       zeroForOne: true,
       amountIn: X,
       ...
     })
5. Router calls pool.swap(bob, true, X, ...) — msg.sender inside pool = router.
6. Pool calls _beforeSwap(router, bob, ...).
7. SwapAllowlistExtension.beforeSwap(sender=router, ...) checks
   allowedSwapper[pool][router] → true.
8. Swap executes. Bob receives output tokens despite not being on the allowlist.
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
