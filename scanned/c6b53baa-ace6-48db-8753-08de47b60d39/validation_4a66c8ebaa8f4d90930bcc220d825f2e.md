### Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), the per-user gate is completely bypassed: every user on the network can swap on the restricted pool by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
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

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the **router**, not the original user:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
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

The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][actual_user]`.

**Resulting dilemma for the pool admin:**

| Admin action | Consequence |
|---|---|
| Allowlist the router address | Every user on the network can bypass the per-user gate by routing through the router |
| Do not allowlist the router | Allowlisted users cannot use the router; they must call `pool.swap()` directly |

The first branch is the critical bypass: a single admin call to `setAllowedToSwap(pool, router, true)` — which is the natural step to enable router usage — silently opens the pool to all callers.

Note the contrast with `DepositAllowlistExtension`, which correctly checks `owner` (the position owner, the economically relevant actor) rather than `sender` (the payer/router):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The swap extension has no equivalent "original user" parameter to fall back on because the `beforeSwap` interface does not carry the original `msg.sender` of the router call.

---

### Impact Explanation

Any user can execute swaps on a pool that is configured with `SwapAllowlistExtension` by routing through `MetricOmmSimpleRouter`, provided the router is allowlisted (the necessary condition for router-mediated swaps to work at all). The allowlist — the sole access-control mechanism for swap gating — is rendered inoperative. Unauthorized swaps can drain LP positions, extract value from restricted pools, or violate compliance constraints the pool admin intended to enforce.

---

### Likelihood Explanation

The router is a standard, publicly deployed periphery contract. Pool admins who configure a swap allowlist and also want their allowlisted users to use the router must allowlist the router address. This is the expected operational path. The bypass is therefore reachable by any unprivileged user on any pool where the router has been allowlisted, which is the common case for production deployments.

---

### Recommendation

The `beforeSwap` hook should gate on the **original end user**, not the intermediary caller. Two options:

1. **Pass the original user via `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires router cooperation and is trust-dependent.

2. **Check `recipient` instead of `sender`**: For swap allowlists, `recipient` is the economically relevant actor receiving tokens. Replace `sender` with `recipient` in the allowlist lookup. This is consistent with how `DepositAllowlistExtension` uses `owner` rather than `sender`.

Option 2 is the simpler, trust-free fix and mirrors the correct pattern already used in `DepositAllowlistExtension`.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps for allowlisted users.
3. Pool admin calls `setAllowedToSwap(pool, alice, true)` to allowlist Alice; Bob is **not** allowlisted.
4. Bob calls `MetricOmmSimpleRouter.exactInputSingle(...)` targeting the restricted pool.
5. The pool calls `_beforeSwap(router, ...)`. The extension checks `allowedSwapper[pool][router]` → `true`. Bob's swap succeeds.
6. Bob receives output tokens from the restricted pool despite never being allowlisted.

The allowlist is fully bypassed for any user who routes through the router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
