Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Originating User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is always `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that `msg.sender` is the router contract address, not the originating EOA. Any pool admin who allowlists the router to enable router-mediated swaps for their permitted users inadvertently grants unrestricted swap access to every address on the network, completely defeating the allowlist invariant.

## Finding Description

**Root cause — `MetricOmmPool.swap` passes `msg.sender` unconditionally:**

In `MetricOmmPool.swap` (lines 230–240), the pool calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
_beforeSwap(
    msg.sender,   // always the immediate caller — the router when routed
    recipient,
    ...
);
```

**`SwapAllowlistExtension.beforeSwap` checks that value against the allowlist:**

```solidity
// lines 37–39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

**The router never encodes the originating user into `extensionData`:**

`MetricOmmSimpleRouter.exactInputSingle` (lines 71–80) passes `params.extensionData` verbatim to the pool — it does not inject `msg.sender`. The `bytes calldata` parameter in `beforeSwap` is unnamed and never read by `SwapAllowlistExtension`.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension` as the `beforeSwap` hook.
2. Admin calls `setAllowedToSwap(pool, userA, true)` to permit a specific user.
3. Admin calls `setAllowedToSwap(pool, router, true)` so that `userA` can benefit from slippage protection via the router — the only available mechanism.
4. Attacker (not in allowlist) calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(attacker, ...)` → pool calls `_beforeSwap(router, ...)` → extension checks `allowedSwapper[pool][router] == true` → no revert → swap executes.

The originating attacker's address is never inspected. The allowlist provides zero protection for all router-mediated volume.

## Impact Explanation

`SwapAllowlistExtension` is the sole on-chain mechanism for a pool admin to restrict which addresses may trade against LP liquidity. Bypassing it allows unauthorized addresses to execute swaps at the oracle-quoted price against a pool configured to be permissioned (e.g., institutional pool, controlled launch, specific counterparty whitelist). Every swap that should have been blocked can drain token0 or token1 from LP positions at the oracle price, causing direct loss of LP principal. The bypass is unconditional once the router is allowlisted, collapsing the entire allowlist invariant for all router-mediated volume. **Severity: High.**

## Likelihood Explanation

The router is the standard, documented user-facing entry point for swaps. Any pool admin who deploys a permissioned pool and wants allowlisted users to benefit from slippage protection or deadline enforcement must allowlist the router — there is no alternative. The bypass requires no special privileges, no flash loans, and no oracle manipulation; any EOA can call the router. **Likelihood: High.**

## Recommendation

The extension must verify the originating user, not the intermediate contract. The most robust fix is to have `MetricOmmSimpleRouter` encode `msg.sender` into `extensionData` before forwarding to the pool, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a known router. Alternatively, the pool architecture could be extended to propagate the true originator through a dedicated field rather than reusing `msg.sender`.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension as beforeSwap hook
  admin: setAllowedToSwap(pool, userA, true)
  admin: setAllowedToSwap(pool, router, true)   ← required for userA to use router

Attack:
  attacker (not in allowlist) calls:
    MetricOmmSimpleRouter.exactInputSingle({pool, recipient=attacker, zeroForOne=true, amountIn=X, ...})

  Router → pool.swap(attacker, true, X, ...)
    pool._beforeSwap(msg.sender=router, ...)
      SwapAllowlistExtension.beforeSwap(sender=router, ...)
        allowedSwapper[pool][router] == true  ✓  → no revert

  Result: attacker's swap executes at oracle price, draining LP token1.
          The allowlist provided zero protection.
```

Confirmed code locations: [1](#0-0) [2](#0-1) [3](#0-2)

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
