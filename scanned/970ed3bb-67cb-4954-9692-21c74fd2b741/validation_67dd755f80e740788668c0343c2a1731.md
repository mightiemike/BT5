### Title
SwapAllowlistExtension Gates the Router Address Instead of the Real User, Allowing Any Unprivileged Caller to Bypass a Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap`, so the extension checks the router's allowlist status, not the actual user's. If the pool admin allowlists the router to enable router-mediated swaps, every unprivileged address can bypass the allowlist by calling through the router.

---

### Finding Description

**Pool.swap passes `msg.sender` as `sender` to the extension:** [1](#0-0) 

`msg.sender` here is whoever called `pool.swap` — the router when a user routes through `MetricOmmSimpleRouter`.

**SwapAllowlistExtension checks `sender` (the router) against the allowlist:** [2](#0-1) 

The check is `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router address — not the end user.

**MetricOmmSimpleRouter calls `pool.swap` directly, making itself the `msg.sender`:** [3](#0-2) 

The actual user (`msg.sender` of `exactInputSingle`) is stored only in transient callback context for payment settlement — it is never forwarded to the pool as the swap `sender`.

**Two broken outcomes result:**

1. **Allowlist bypass**: If the pool admin allowlists the router address (the only way to let any user swap through the router), every address — including those the admin explicitly never allowlisted — can call `router.exactInputSingle` and pass the extension check, because the extension sees `sender = router` which is allowlisted.

2. **Allowlisted users locked out**: If the admin allowlists individual user addresses (the intended design), those users cannot swap through the router at all, because the extension sees `sender = router` (not allowlisted) and reverts with `NotAllowedToSwap`.

The analog to the WrappedVault bug is exact: just as WrappedVault tracked Dahlia shares against the original depositor while the wrappedVault balance moved to a new owner (wrong entity tracked), `SwapAllowlistExtension` tracks allowlist membership against the router while the economic actor is the end user (wrong identity gated).

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., a permissioned institutional pool) can be fully bypassed by any unprivileged address routing through `MetricOmmSimpleRouter`. The attacker receives pool output tokens and the pool receives input tokens — real token flows occur. The allowlist guard, the only access-control mechanism on the swap path, is rendered ineffective. This is a broken core pool functionality and an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) defeats a pool-admin-configured guard.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point documented in the periphery. Any user who discovers the allowlist can trivially route through the router. No special privileges, flash loans, or multi-step setup are required — a single `exactInputSingle` call suffices. The pool admin has no on-chain mechanism to prevent this without removing the router from the ecosystem entirely.

---

### Recommendation

Forward the real user identity through the swap path. Two options:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires extension cooperation and is opt-in per deployment.

2. **Add a `realSender` field to the pool's `swap` interface**: The pool accepts an explicit `sender` parameter (validated against `msg.sender` or a trusted-forwarder registry) and passes it to extensions. This is the cleanest fix but requires a core interface change.

The `DepositAllowlistExtension` does not share this bug because it checks `owner` (the position owner explicitly passed to `addLiquidity`), not `sender`. [4](#0-3) 

---

### Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Admin allowlists the router so that allowlisted users can swap through it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attacker (never allowlisted) calls the router directly.
// The extension sees sender = router (allowlisted) → passes.
// Attacker receives pool output tokens.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: token0,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Swap succeeds. Allowlist completely bypassed.
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
