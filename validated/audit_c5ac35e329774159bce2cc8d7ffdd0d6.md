Audit Report

## Title
`SwapAllowlistExtension` gates the router address instead of the actual user, enabling full allowlist bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender` — the router — not the end user. When a pool admin allowlists the router so that permitted users can reach the pool through the standard periphery path, every unpermissioned address can bypass the allowlist by routing through `MetricOmmSimpleRouter`, completely defeating the curation policy.

## Finding Description

`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` is used, the router calls `pool.swap()` directly, making the router the pool's `msg.sender`: [4](#0-3) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. A pool admin who wants permitted users to reach the pool through the router must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check passes for every caller — any unpermissioned address can swap by routing through `MetricOmmSimpleRouter`. The same actor-mismatch applies to multi-hop `exactInput` (all hops call `pool.swap()` from the router) and `exactOutputSingle`.

`DepositAllowlistExtension` does not share this flaw because it gates `owner` — the position recipient passed explicitly — not `sender`: [5](#0-4) 

## Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that protection entirely once the router is allowlisted. Any unpermissioned address can execute swaps, drain favorable oracle-priced liquidity, or interact with a pool whose terms were never meant to apply to them. This constitutes a direct loss of LP principal and a broken core pool invariant — the allowlist guard fails open on the supported periphery path. Severity: **High**.

## Likelihood Explanation

Adding the router to the allowlist is the only way to let permitted users reach the pool through the standard periphery. Any operator who deploys a curated pool and also wants router support will make this configuration, making the bypass reachable by any public user with no special privilege. The attacker needs no elevated access — only the ability to call `MetricOmmSimpleRouter.exactInputSingle` with the target pool address.

## Recommendation

The extension must gate the economically relevant actor — the end user — not the intermediary router. Two viable approaches:

1. **Pass the originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. The pool admin must trust that the router populates this field honestly (acceptable because the router is a known, audited contract).
2. **Dedicated `originalSender` forwarding field**: Add an explicit `originalSender` field to the pool's `swap` call that the router populates with `msg.sender`, and forward it to extensions separately from the pool-level `msg.sender`.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension; set allowAllSwappers[pool] = false.
2. Admin calls setAllowedToSwap(pool, alice, true)   // Alice is KYC'd
3. Admin calls setAllowedToSwap(pool, router, true)  // needed so Alice can use the router
4. Bob (not KYC'd) calls router.exactInputSingle({pool: pool, recipient: bob, ...})
5. Router calls pool.swap(bob, zeroForOne, amount, limit, "", extensionData)
   → pool.msg.sender = router
6. Pool calls _beforeSwap(router, bob, ...) → extension.beforeSwap(router, bob, ...)
   → allowedSwapper[pool][router] == true → no revert
7. Bob's swap executes in the curated pool; allowlist is bypassed.
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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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
