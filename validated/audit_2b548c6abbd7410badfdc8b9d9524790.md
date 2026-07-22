### Title
`SwapAllowlistExtension.beforeSwap` checks `sender` (the router intermediary) instead of the originating end-user, allowing any unprivileged caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates `pool.swap()` by checking the `sender` argument, which is `msg.sender` of the pool call — the direct caller. When a user routes through `MetricOmmSimpleRouter`, `sender` equals the router address, not the end-user. If the router is allowlisted (the only way to permit router-based swaps), every unprivileged address can bypass the allowlist by calling through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension is called by the pool) and `sender` is the `msg.sender` of `pool.swap()` — i.e., the direct caller of the pool, not the originating user.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  lines 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [2](#0-1) 

So when any user calls the router, `sender` forwarded to the extension is the **router address**, not the user. The pool passes `msg.sender` (= router) as `sender` to `_beforeSwap`: [3](#0-2) 

which encodes it into the extension call: [4](#0-3) 

The allowlist therefore checks `allowedSwapper[pool][router]`. If the pool admin allowlists the router (the only way to permit any router-based swap), the gate is permanently open to **all** callers of that router, regardless of whether they are individually allowlisted.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` — the position owner passed explicitly by the caller — which is the actual end-user even when the `MetricOmmPoolLiquidityAdder` is the intermediary: [5](#0-4) 

The swap extension uses the wrong field (`sender` = intermediary) while the deposit extension uses the right field (`owner` = end-user). This is the direct analog of the external report's "wrong parameter stored" class: the guard reads the pre-routing intermediary address instead of the post-routing originator address.

---

### Impact Explanation

A pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-gated, institutional-only, or regulatory-compliance pools) cannot simultaneously allow users to trade through the canonical `MetricOmmSimpleRouter` and enforce per-user restrictions. Any address can call `router.exactInputSingle / exactInput / exactOutputSingle / exactOutput`, causing the pool to see `sender = router`, and if the router is allowlisted the guard passes unconditionally. The allowlist is rendered inoperative for all router-mediated swaps, allowing unauthorized principals to trade against LP capital in a pool that was explicitly configured to exclude them.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the standard user-facing entry point; most swaps are expected to flow through it.
- A pool admin who wants users to be able to swap at all via the router must allowlist the router address, which simultaneously opens the bypass to every caller.
- No special privilege, flash loan, or oracle manipulation is required — any EOA or contract can call the router.

---

### Recommendation

The extension must identify the originating user, not the intermediary. Two viable approaches:

1. **Pass originator in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a convention between router and extension.
2. **Check `recipient` with a documented convention**: For swap allowlists, gate on `recipient` (the address receiving output tokens) rather than `sender`, and document that `recipient` must equal the originating user. This is already available in the `beforeSwap` signature as the second argument (currently ignored).

The simplest safe fix consistent with how `DepositAllowlistExtension` works is to check `recipient` (the second parameter, currently unnamed/ignored) instead of `sender`, since the router always sets `recipient` to the user-controlled address:

```solidity
// proposed fix
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice]  = true   // alice is KYC'd
  allowedSwapper[pool][router] = true   // admin must set this for any router swap to work
  bob is NOT in the allowlist

Attack:
  bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  → router calls pool.swap(bob, ...)
  → pool calls extension.beforeSwap(sender=router, recipient=bob, ...)
  → extension checks allowedSwapper[pool][router] == true  ✓
  → swap executes — bob trades in a pool he is not authorized to access
```

The bypass requires zero privileged access and is reachable through the standard production periphery path.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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
