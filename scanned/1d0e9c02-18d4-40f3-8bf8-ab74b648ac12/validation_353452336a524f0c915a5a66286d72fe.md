### Title
SwapAllowlistExtension Checks the Router Address Instead of the Actual User, Allowing Any User to Bypass the Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the pool — the router contract — not the end user. When a pool admin allowlists the router to enable router-mediated swaps, every public user can bypass the allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

In `MetricOmmPool.swap`, the `_beforeSwap` hook is dispatched with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether that `sender` is allowlisted for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly: [4](#0-3) 

Inside the pool, `msg.sender` is the router, so `sender` delivered to the extension is the router address — not the end user. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for any pool admin who deploys a `SwapAllowlistExtension`:

| Admin action | Effect |
|---|---|
| Allowlist the router | Every public user can bypass the allowlist by routing through `MetricOmmSimpleRouter` |
| Do not allowlist the router | Individually allowlisted users cannot use the router at all |

Neither configuration enforces the intended policy. The same wrong-actor binding applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — in every case the pool's `msg.sender` is the router. [5](#0-4) 

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` — the economically relevant actor — so the deposit path does not share this flaw: [6](#0-5) 

---

### Impact Explanation

A pool admin who deploys a curated pool (e.g., institutional-only, KYC-gated, or counterparty-restricted) and configures `SwapAllowlistExtension` to restrict trading to specific addresses loses that protection entirely for any user who routes through the public `MetricOmmSimpleRouter`. The allowlist — the sole on-chain enforcement mechanism for the pool's access policy — is silently bypassed. Unauthorized traders can execute swaps against LP positions, causing direct LP losses through adverse selection or violating the pool's intended access boundary. This is an admin-boundary break reachable by any unprivileged user via a supported public periphery path.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call `exactInputSingle` or `exactInput` with no preconditions. The bypass requires no special privileges, no flash loan, and no multi-step setup — a single router call suffices. Any pool that (a) uses `SwapAllowlistExtension` and (b) allowlists the router (which is necessary for router-mediated swaps to function) is immediately exploitable by any address.

---

### Recommendation

The `SwapAllowlistExtension` must gate the economically relevant actor, not the intermediary. Two sound approaches:

1. **Check `recipient` instead of `sender`** — the recipient is the address that receives swap output and is harder to spoof than the routing intermediary. However, this still does not capture the payer identity.
2. **Require the router to forward the originating user** — add an `originSender` field to `extensionData` that the router populates with `msg.sender`, and have the extension decode and check that value. The pool's `onlyPool` guard on the extension already ensures only a legitimate pool can invoke the hook, so the extension can trust the pool to forward the router-supplied bytes faithfully.

The `DepositAllowlistExtension` pattern (checking `owner`, the position recipient) is the correct model: gate the actor who receives the economic benefit, not the contract that routes the call.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (required so that router-mediated swaps work at all).
  - Pool admin calls setAllowedToSwap(pool, alice, true).
  - Pool admin does NOT call setAllowedToSwap(pool, bob, true).
    Bob is explicitly disallowed.

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...}).
  2. Router calls pool.swap(recipient=bob, ...).
  3. Pool calls _beforeSwap(sender=router, ...).
  4. Extension checks allowedSwapper[pool][router] → true (router is allowlisted).
  5. Swap executes. Bob trades on the restricted pool despite being disallowed.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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
