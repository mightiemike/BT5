Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` parameter, which resolves to `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If the pool admin allowlists the router — a natural operational step to enable router-mediated swaps — every address in existence can bypass the per-user allowlist by calling the public router.

## Finding Description
`SwapAllowlistExtension.beforeSwap()` performs:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is the first argument forwarded by the pool. In `MetricOmmPool.swap()`, the pool passes its own `msg.sender` as `sender`:

```solidity
_beforeSwap(
    msg.sender,   // ← becomes `sender` in the extension
    recipient,
    ...
);
``` [2](#0-1) 

In `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly, making itself `msg.sender`:

```solidity
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
``` [3](#0-2) 

The full call chain when routing: User → `router.exactInputSingle()` → `pool.swap(msg.sender=router)` → `extension.beforeSwap(sender=router)` → checks `allowedSwapper[pool][router]`. The extension sees the router address, not the actual user. Since `MetricOmmSimpleRouter` is a public, permissionless contract, any address can call it. Once the admin allowlists the router, the per-user gate is fully open to all callers.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` argument (the position owner), which is passed explicitly by the caller and is not collapsed to the intermediary's address. [4](#0-3) 

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict swaps to specific counterparties (KYC-verified addresses, designated market makers, protocol-controlled accounts) loses that restriction entirely for the router path once the router is allowlisted. Unauthorized traders gain full swap access to the pool. Because swaps are oracle-anchored, the direct per-swap loss is bounded by the spread, but LP exposure to unintended counterparties is unbounded over time. This constitutes broken core pool access-control functionality with indirect but unbounded LP fund exposure, meeting the "broken core pool functionality" impact category.

## Likelihood Explanation
Medium. The bypass requires the pool admin to allowlist the router address — a natural and expected operational step for any pool that wants its LPs reachable via the standard periphery router. The admin is likely unaware that doing so grants unrestricted swap access to every address, because the extension's name and setter (`setAllowedToSwap`) imply per-address granularity. No attacker capability beyond calling the public router is required once the router is allowlisted.

## Recommendation
1. **Short-term**: Document explicitly that allowlisting the router grants swap access to all router users, not to specific individuals. Warn pool admins against allowlisting shared public routers when per-user gating is intended.
2. **Medium-term**: Extend the router to forward the originating user's address in `extensionData` (e.g., `abi.encode(msg.sender)`). Update `SwapAllowlistExtension` to decode and check this field when `sender` is a known router, falling back to `sender` otherwise.
3. **Long-term**: Consider a two-level check: gate on `sender` for direct callers and on a decoded user field for router-mediated calls, with the router signing or attesting the user identity so the extension can trust it.

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Admin calls extension.setAllowedToSwap(pool, router, true)
   — intending to allow router-mediated swaps for their users.
3. Attacker (address never individually allowlisted) calls:
     router.exactInputSingle({pool: pool, ...})
4. Call chain:
     router → pool.swap(msg.sender=router) →
     extension.beforeSwap(sender=router) →
     allowedSwapper[pool][router] == true → passes
5. Attacker's swap executes on the restricted pool.
   No individual allowlist entry for the attacker exists;
   the per-user gate is fully bypassed.
```

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
