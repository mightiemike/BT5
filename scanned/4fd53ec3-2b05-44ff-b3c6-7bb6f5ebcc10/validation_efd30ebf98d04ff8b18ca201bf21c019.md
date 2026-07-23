### Title
`SwapAllowlistExtension` gates on the router address instead of the end-user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. If the pool admin allowlists the router address to enable router-mediated swaps, every user on-chain can bypass the per-user allowlist by calling the router. Conversely, if the admin does not allowlist the router, allowlisted users cannot use the router at all. Neither configuration achieves the intended per-user access control.

---

### Finding Description

**Pool's `swap()` passes `msg.sender` as `sender` to the extension:** [1](#0-0) 

`_beforeSwap` is called with `msg.sender` (the immediate caller of `pool.swap()`) as the `sender` argument.

**`SwapAllowlistExtension.beforeSwap` checks `sender` against the allowlist:** [2](#0-1) 

Inside the extension, `msg.sender` is the pool (correct), and `sender` is whoever called `pool.swap()`. The check is `allowedSwapper[pool][sender]`.

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` as `msg.sender = router`:** [3](#0-2) 

The router calls `pool.swap(...)` directly. The pool receives `msg.sender = router`. The extension therefore sees `sender = router`, not the end-user who called the router.

**The same applies to `exactInput` and `exactOutput` multi-hop paths:** [4](#0-3) 

Every hop calls `pool.swap()` with `msg.sender = router`, so the extension always sees the router address as `sender`.

**Contrast with `DepositAllowlistExtension`, which correctly gates on `owner`:** [5](#0-4) 

The deposit extension ignores `sender` (the first argument, which is the pool's `msg.sender`) and checks `owner` — the actual position owner passed explicitly by the pool. This is correct because `MetricOmmPoolLiquidityAdder` always passes the real user as `owner`. The swap extension has no equivalent "real user" argument; it only receives `sender` = the pool's `msg.sender`.

---

### Impact Explanation

A pool admin who deploys a pool with `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd users, trusted market makers, or to protect LPs from informed-flow adverse selection) faces an inescapable dilemma:

1. **If the admin allowlists the router**: every user on-chain can call `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutput` and the extension passes because `sender = router` is allowlisted. The per-user allowlist is completely bypassed. Unauthorized users trade against LP positions, causing LP principal loss through adverse selection or pool manipulation.

2. **If the admin does not allowlist the router**: allowlisted users cannot use the router at all (their swaps revert with `NotAllowedToSwap`), breaking core pool usability for the intended participants.

In scenario 1, LP assets are directly at risk from unauthorized traders. The allowlist guard — the only mechanism protecting LPs in a permissioned pool — is rendered ineffective.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary user-facing swap interface; most users are expected to interact through it rather than calling `pool.swap()` directly.
- A pool admin who configures a swap allowlist and also wants to support router-mediated swaps (the normal use case) will naturally allowlist the router, triggering the bypass.
- No special privileges or unusual conditions are required: any user with a standard ERC-20 approval to the router can exploit this.

---

### Recommendation

The `beforeSwap` hook should receive the true end-user identity. Two approaches:

1. **Pass the original `msg.sender` through `extensionData`**: The router encodes the actual user address in `extensionData`, and the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Gate on `sender` but require the router to forward the real user**: Add a `swapFor(address realUser, ...)` entry point on the pool or router that passes `realUser` as the `sender` argument to the extension, authenticated by the router's own allowlist check.

3. **Align with the deposit pattern**: Introduce a `swapper` parameter (analogous to `owner` in `addLiquidity`) that the pool passes explicitly to the extension, separate from `msg.sender`, allowing the router to supply the real user address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook
  - Pool admin allowlists router: allowedSwapper[pool][router] = true
  - Pool admin does NOT allowlist attacker: allowedSwapper[pool][attacker] = false

Attack:
  1. attacker calls router.exactInputSingle({pool: pool, ...})
  2. router calls pool.swap(recipient, ...) with msg.sender = router
  3. pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap checks allowedSwapper[pool][router] → true → PASSES
  5. Swap executes; attacker receives output tokens

Result:
  - attacker (not allowlisted) successfully swaps against the permissioned pool
  - LP assets are exposed to unauthorized trading flow
  - The swap allowlist guard is completely bypassed
``` [6](#0-5) [7](#0-6) [3](#0-2)

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
