### Title
SwapAllowlistExtension Gates on Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` resolves to the router's address, not the actual end user. If the pool admin allowlists the router — a natural configuration to let legitimate users access multi-hop or exact-output routing — every unpermissioned user can bypass the swap allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every configured extension:

```solidity
// MetricOmmPool.sol:231
_beforeSwap(
    msg.sender,   // ← sender = whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then gates on that value:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool (the caller of the extension), so the effective check is:

```
allowedSwapper[pool][sender]   // sender = whoever called pool.swap()
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point), the router calls `pool.swap(...)` directly:

```solidity
// MetricOmmSimpleRouter.sol:72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

The pool receives `msg.sender = router`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Bypass path:**
1. Pool admin deploys pool with `SwapAllowlistExtension` to restrict swaps to KYC'd addresses.
2. Admin allowlists specific user addresses.
3. Admin also allowlists `MetricOmmSimpleRouter` so that allowlisted users can access multi-hop routing.
4. Any non-allowlisted user calls `router.exactInputSingle(pool, ...)`.
5. Router calls `pool.swap(...)` → extension sees `sender = router` → router is allowlisted → swap succeeds.

The allowlist is completely defeated for every user who routes through the router.

**Contrast with `DepositAllowlistExtension`**, which correctly ignores `sender` and gates on `owner` (the actual position beneficiary):

```solidity
// DepositAllowlistExtension.sol:32-41
function beforeAddLiquidity(address, address owner, ...)  // sender ignored
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

The swap extension has no equivalent "owner" field to fall back on, so it checks the wrong actor.

---

### Impact Explanation

A curated pool whose admin allowlists the router loses all swap-access control. Any address — including those explicitly excluded from the allowlist — can execute swaps by routing through `MetricOmmSimpleRouter`. This breaks the core invariant of the extension: that only approved addresses may trade on the pool. Depending on the pool's purpose (e.g., institutional-only, regulatory-compliant, or pre-launch restricted), this constitutes a direct policy breach with potential fund-impacting consequences (unauthorized price impact, extraction of LP value by disallowed parties).

---

### Likelihood Explanation

The trigger requires the router to be allowlisted. This is a natural and expected configuration: a pool admin who wants allowlisted users to access multi-hop or exact-output routing must allowlist the router. The admin is unlikely to realize that doing so opens the gate to all users, because the `DepositAllowlistExtension` (the sibling extension) correctly gates on `owner` and does not exhibit this problem. The asymmetry between the two extensions makes the flaw non-obvious.

---

### Recommendation

`SwapAllowlistExtension` should not check `sender` (the immediate caller of `pool.swap()`). Instead, it should check the `recipient` — the address that actually receives the output tokens — or require the router to embed the real user identity in `extensionData` and verify it. The simplest safe fix is to gate on `recipient`:

```solidity
function beforeSwap(address, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][recipient]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, document explicitly that the router must never be allowlisted and that router-based swaps are incompatible with per-user swap gating.

---

### Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension configured.
2. Admin calls swapExtension.setAllowedToSwap(pool, alice, true)   // alice is KYC'd
3. Admin calls swapExtension.setAllowedToSwap(pool, router, true)  // enable router for alice
4. Bob (not allowlisted) calls:
       router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, ...) with msg.sender = router.
6. Extension checks allowedSwapper[pool][router] → true → swap succeeds.
7. Bob receives output tokens despite never being allowlisted.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
