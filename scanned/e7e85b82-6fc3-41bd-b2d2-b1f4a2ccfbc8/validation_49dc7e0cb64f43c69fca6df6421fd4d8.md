### Title
`SwapAllowlistExtension.beforeSwap` Checks Router Address Instead of End User, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that value is the router address, not the end user. A pool admin who allowlists the router to enable router-based swaps for their curated users inadvertently opens the pool to every user, defeating the allowlist entirely.

---

### Finding Description

**The guard check:**

`SwapAllowlistExtension.beforeSwap` reads:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct). `sender` is the first argument forwarded by the pool, which the pool sets to its own `msg.sender` at the time `swap()` is called:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
```

**What the router sends:**

Every `MetricOmmSimpleRouter` entry point calls `pool.swap()` directly from the router contract:

```solidity
// exactInputSingle
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

So `msg.sender` of `pool.swap()` = router address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

**The forced choice that breaks the invariant:**

A pool admin who wants to allow specific users (alice, bob) to swap faces an impossible choice:

| Admin action | Effect |
|---|---|
| Allowlist alice and bob only | Alice and bob can swap directly; router calls revert for them too (router not allowlisted) |
| Allowlist the router | Alice, bob, AND every other user can bypass the allowlist via the router |

There is no configuration that allows specific users to use the router while blocking others. Allowlisting the router is the only way to enable router-based swaps, and it opens the pool to all users.

**Contrast with `DepositAllowlistExtension`:**

The deposit extension correctly checks `owner` (the position recipient), not `sender` (the direct caller):

```solidity
// DepositAllowlistExtension.sol
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
```

This works correctly through `MetricOmmPoolLiquidityAdder` because `owner` is the position owner regardless of who the direct caller is. The swap extension has no equivalent end-user parameter to check.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (a reasonable operational step to support the supported periphery) inadvertently allows any unprivileged user to bypass the allowlist. The allowlist invariant — that only approved addresses may swap — is broken for all router-mediated swaps. On pools designed to restrict trading to KYC'd counterparties, institutional participants, or specific protocol actors, this allows arbitrary users to execute swaps against LP principal.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap periphery. A pool admin who wants their allowlisted users to be able to use the router has no option other than allowlisting the router address. This is a natural operational step, not an exotic configuration. The bypass is then reachable by any user with no further preconditions.

---

### Recommendation

The `SwapAllowlistExtension` should gate the economically relevant actor — the end user — not the direct caller of `pool.swap()`. Two viable approaches:

1. **Router-forwarded identity via `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` for the swap allowlist extension. The extension decodes and checks that address. This requires a coordinated change to both the router and the extension.

2. **Separate router allowlist with per-user check in `extensionData`**: The extension checks `sender` for direct calls and decodes an authenticated user address from `extensionData` for router calls, with the router signing or encoding the original caller.

At minimum, the extension's NatSpec must document that allowlisting the router grants access to all router users, so pool admins are not misled about the protection boundary.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls swapExtension.setAllowedToSwap(pool, alice, true)
   and swapExtension.setAllowedToSwap(pool, bob, true).
3. Alice tries router.exactInputSingle({pool: pool, ...}) → reverts
   NotAllowedToSwap (router not allowlisted). Admin observes this.
4. Admin calls swapExtension.setAllowedToSwap(pool, router, true)
   to fix the router path for alice and bob.
5. Charlie (never allowlisted) calls router.exactInputSingle({pool: pool, ...}).
   Router calls pool.swap(); pool passes sender=router to beforeSwap.
   Extension checks allowedSwapper[pool][router] → true.
   Charlie's swap executes successfully, bypassing the allowlist.
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
