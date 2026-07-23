### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is the direct `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` calls the pool, `sender` is the **router address**, not the end user. A pool admin who whitelists the router to enable router-based swaps inadvertently grants every user the ability to bypass the individual swap allowlist.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` enforces:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool — which is `msg.sender` of the `pool.swap()` call itself.

`MetricOmmPool.swap()` passes `msg.sender` as `sender` to every extension:

```solidity
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or any other router entry point) calls `pool.swap()`, the pool's `msg.sender` is the **router**, not the end user:

```solidity
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
``` [3](#0-2) 

So the allowlist check resolves to `allowedSwapper[pool][router]`. For any user to reach the pool through the router, the pool admin must whitelist the router. The moment the router is whitelisted, **every user** — including those not individually allowlisted — can swap freely by routing through it.

The asymmetry with `DepositAllowlistExtension` confirms this is a wrong-actor binding. The deposit extension correctly checks `owner` (the position owner, i.e., the economic actor), not `sender` (the liquidity-adder contract):

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [4](#0-3) 

`SwapAllowlistExtension` should mirror this pattern but does not.

---

### Impact Explanation

A curated pool using `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC'd users, approved market makers) is fully bypassed for any user who routes through `MetricOmmSimpleRouter`. Unauthorized users can drain LP value by trading at oracle prices the pool was not intended to serve them. This is a direct loss of LP principal and a broken core pool functionality impact.

---

### Likelihood Explanation

The router is the standard, documented periphery entry point. Pool admins who want to support multi-hop swaps, slippage protection, or deadline enforcement must whitelist the router. The non-obvious consequence — that whitelisting the router opens the pool to all users — makes this a realistic misconfiguration. The asymmetry with `DepositAllowlistExtension` (which correctly checks `owner`) makes the flaw easy to miss during review.

---

### Recommendation

Check the economic actor, not the direct pool caller. Two options:

1. **Mirror `DepositAllowlistExtension`**: use `recipient` as the gated identity (the address receiving output tokens), which the router always sets to the end user.
2. **Signed extensionData**: require the router to embed the user's address in `extensionData` with a verifiable signature, and check that address in the extension.

Option 1 is simpler and consistent with the deposit pattern already in the codebase.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists only `alice`: `extension.setAllowedToSwap(pool, alice, true)`.
3. Pool admin also allowlists the router so router-based swaps work: `extension.setAllowedToSwap(pool, router, true)`.
4. Unauthorized `bob` calls `router.exactInputSingle({pool: pool, recipient: bob, ...})`.
5. Router calls `pool.swap(bob, zeroForOne, ...)` — pool's `msg.sender = router`.
6. Pool calls `extension.beforeSwap(router, bob, ...)` — extension checks `allowedSwapper[pool][router]` → **true**.
7. Bob's swap executes on the allowlisted pool despite never being individually allowlisted.

The invariant "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it" is broken: direct callers are gated per-user, but router callers are gated only at the router level, collapsing all users into a single identity.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
