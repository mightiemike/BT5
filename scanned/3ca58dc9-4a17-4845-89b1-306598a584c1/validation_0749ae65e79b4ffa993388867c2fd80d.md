### Title
`SwapAllowlistExtension` checks the router address as `sender` instead of the end user, allowing swap allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the router is allowlisted (or `allowAllSwappers` is true), any non-allowlisted user can bypass the curated pool's swap restriction by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured `beforeSwap` extension:

```solidity
// MetricOmmPool.sol
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks that exact `sender` value against the per-pool allowlist:

```solidity
// SwapAllowlistExtension.sol
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the router the `msg.sender` of that call:

```solidity
// MetricOmmSimpleRouter.sol
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool therefore passes `address(router)` as `sender` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

**Two concrete broken invariants result:**

1. **Bypass (fund-impacting):** If the pool admin allowlists the router address (a natural step to enable router-based swaps for legitimate users), every non-allowlisted user can bypass the curated restriction by routing through `MetricOmmSimpleRouter`. The extension sees the router as the sender and passes the check.

2. **Broken core functionality:** If the pool admin does NOT allowlist the router, every allowlisted user is blocked from using the router even though they are individually permitted. The extension rejects the router address, making the primary periphery swap path unusable for all users of that pool.

This is structurally identical to the external report's soulbound bug: the guard applies a single check (`!isSoulbound`) without distinguishing the operation context (mint vs. transfer vs. burn). Here, the guard applies a single `sender` check without distinguishing whether the immediate caller is the end user or an intermediary router.

---

### Impact Explanation

**Direct loss / broken core functionality — Medium/High.**

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., whitelisted market makers, KYC'd addresses, or protocol-owned bots) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. An unauthorized user can:

- Execute swaps against oracle-priced liquidity that the pool admin intended to restrict, extracting value from LP positions.
- Drain the pool's token reserves in directions the stop-loss or other guards were not configured to protect against, because the allowlist was the first line of defense.

---

### Likelihood Explanation

**High.** `MetricOmmSimpleRouter` is the canonical periphery swap path. Any user aware of the router can trivially route through it. The pool admin allowlisting the router is a natural and expected configuration step (otherwise no user can swap via the router at all). The two failure modes are mutually exclusive but both reachable with normal, unprivileged user actions.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end user, not the immediate pool caller. Two options:

1. **Pass end-user identity through `extensionData`:** The router encodes `msg.sender` (the end user) into `extensionData` before calling `pool.swap`. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `sender` only when it is not a known router:** Maintain a registry of trusted routers in the extension; when `sender` is a trusted router, decode the real user from `extensionData`; otherwise check `sender` directly.

3. **Align with the deposit allowlist pattern:** `DepositAllowlistExtension` correctly checks `owner` (the position beneficiary) rather than `sender` (the operator). For swaps, the analogous "beneficiary" is the `recipient`. Switching the check to `recipient` would correctly identify the economic beneficiary regardless of routing path, though it changes the semantics of the allowlist.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension as beforeSwap extension
  pool admin calls setAllowedToSwap(pool, address(router), true)
    → router is allowlisted so legitimate users can swap via router
  pool admin does NOT call setAllowedToSwap(pool, attacker, true)
    → attacker is not individually allowlisted

Attack:
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
    → router calls pool.swap(recipient, zeroForOne, amount, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  ✓ passes
    → swap executes for attacker despite not being individually allowlisted

Result:
  attacker swaps in a curated pool that was intended to restrict trading
  to specific counterparties; LP assets are exposed to unauthorized flow
```

**Key code references:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
