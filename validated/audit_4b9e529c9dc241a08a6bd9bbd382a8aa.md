Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any caller to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router calls `pool.swap()` directly, making `msg.sender` to the pool the router contract address. The extension therefore checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`, allowing any unpermissioned user to bypass the allowlist by routing through the public router.

## Finding Description

**Root cause — extension checks pool's immediate caller, not the end user:**

`SwapAllowlistExtension.beforeSwap` evaluates:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])
```
where `msg.sender` is the pool and `sender` is the first argument forwarded by the pool. [1](#0-0) 

**Pool passes its own `msg.sender` as `sender`:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)`, so `sender` = whoever called `pool.swap()`. [2](#0-1) 

**Router calls `pool.swap()` as itself:**

`MetricOmmSimpleRouter.exactInputSingle` stores the real user in transient storage via `_setNextCallbackContext` (for the payment callback), but calls `IMetricOmmPoolActions(params.pool).swap(...)` directly with no mechanism to forward the real `msg.sender` as the `sender` argument to the pool. [3](#0-2) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call `pool.swap()` as the router contract. [4](#0-3) 

**No existing guard compensates:** The extension has no awareness of routers, no decoding of `extensionData` for a real-user address, and no fallback check. The `allowedSwapper` mapping is keyed purely on the address passed as `sender`. [5](#0-4) 

**Two broken configurations result:**
1. **Bypass**: Admin allowlists the router so legitimate users can trade through it → every public user can also call `router.exactInputSingle` and the check passes because `allowedSwapper[pool][router] == true`.
2. **Broken periphery**: Admin only allowlists individual user addresses → those users cannot use the router at all (router is not allowlisted), breaking the primary supported periphery path.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses loses that protection entirely once the router is allowlisted. Any public caller can invoke `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and trade against the pool's liquidity without being on the allowlist. LP funds are exposed to unrestricted trading that the pool admin explicitly configured the extension to prevent — a direct loss of the access-control guarantee protecting LP assets.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported periphery path for end users. Pool admins who want allowlisted users to have a normal UX will allowlist the router. The bypass is immediately reachable by any public caller with no special privileges, no flash loan, and no multi-step setup — a single `exactInputSingle` call suffices. The precondition (router is allowlisted) is the natural and expected configuration for any pool that wants to support the router for its approved users.

## Recommendation
The extension must gate on the end user, not the immediate pool caller. Two sound approaches:

1. **Forward the real user through `extensionData`**: Have the router encode `msg.sender` into `params.extensionData` before calling `pool.swap()`, and have the extension decode and check that address. This requires a convention between the router and the extension.
2. **Router-aware allowlist in the extension**: The extension detects that `sender` is a known router and, in that case, decodes the real user address from the `extensionData` bytes argument (already passed through to `beforeSwap`).

The simplest correct fix is approach 1: the router appends `abi.encode(msg.sender)` to `extensionData`, and the extension, when `sender` is a recognized router, checks the decoded address against `allowedSwapper` instead of `sender`.

## Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension (beforeSwap order = 1)
  pool admin calls: swapExtension.setAllowedToSwap(pool, address(router), true)
    (admin does this so that Alice, an allowlisted user, can use the router)

Attack:
  Bob (address NOT in allowedSwapper[pool]) calls:
    router.exactInputSingle(ExactInputSingleParams{
        pool: pool,
        zeroForOne: true,
        amountIn: X,
        recipient: Bob,
        ...
    })

  Execution trace:
    router.exactInputSingle()
      → _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, Bob, tokenIn)  // Bob stored in transient storage only
      → pool.swap(Bob, true, amountIn, priceLimitX64, "", extensionData)
          msg.sender to pool = address(router)
        → _beforeSwap(sender=address(router), ...)
          → SwapAllowlistExtension.beforeSwap(sender=address(router), ...)
              allowedSwapper[pool][router] == true  ← passes
        → swap executes, Bob receives tokens

Result: Bob, who is not on the allowlist, successfully swaps against the curated pool.
```

Foundry test plan: deploy pool with `SwapAllowlistExtension`, call `setAllowedToSwap(pool, router, true)`, call `router.exactInputSingle` from an address not in `allowedSwapper`, assert the swap succeeds and tokens are transferred to the unpermissioned caller.

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

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
