Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` gates on router address instead of originating user, enabling allowlist bypass and blocking legitimate swappers — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` receives `sender` which is the pool's `msg.sender` — the router contract — not the originating user. When a pool admin allowlists individual user addresses, those users are silently blocked when swapping through `MetricOmmSimpleRouter` because the extension evaluates the router's address. Conversely, allowlisting the router address grants unrestricted access to every caller, defeating the curation policy entirely.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` gates on that `sender` value: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router becomes `msg.sender` of the pool's `swap` call, and passes `""` as `extensionData` — the originating user's address is never forwarded: [4](#0-3) 

So the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The actual end-user identity is never consulted.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the LP position recipient), not `sender`: [5](#0-4) 

The swap extension lacks this identity-preserving design. Two symmetric failures result:

- **Failure A:** Pool admin allowlists `alice`. Alice calls `exactInputSingle`. Extension sees `allowedSwapper[pool][router] == false` → reverts `NotAllowedToSwap`. Alice is permanently blocked from the official periphery path despite being explicitly allowlisted.
- **Failure B:** Pool admin allowlists the router address (natural step to enable router-mediated swaps). Extension sees `allowedSwapper[pool][router] == true` for every caller → any unprivileged user trades on the curated pool with no individual authorization.

## Impact Explanation

**Failure A** constitutes broken core pool functionality: the primary user-facing swap path (`MetricOmmSimpleRouter`) is permanently unusable for the exact population the allowlist was designed to serve. **Failure B** constitutes an allowlist bypass enabling unauthorized traders to drain liquidity at oracle prices or front-run allowlisted participants, causing direct loss of LP principal. Both impacts fall within the allowed impact gate: broken core swap flow and direct loss of user/LP assets.

## Likelihood Explanation

No privileged attacker capability is required. Any pool that configures `SwapAllowlistExtension` with per-address allowlisting immediately exhibits Failure A for every router-mediated swap. Failure B is triggered the moment an admin allowlists the router address — a natural and expected configuration step. A normal user calling `exactInputSingle` is sufficient to trigger either failure.

## Recommendation

Replace the `sender` check with the originating user identity. The preferred fix mirrors `DepositAllowlistExtension`: require the router to encode the real user address into `extensionData`, then decode it in `beforeSwap`. Alternatively, check `recipient` if the pool design guarantees `recipient == user`. The minimal-fix approach of documenting that admins must allowlist the router moves the security boundary off-chain and is not recommended, as it enables Failure B.

## Proof of Concept

```
Failure A:
1. Deploy pool with SwapAllowlistExtension on beforeSwap.
2. Pool admin calls setAllowedToSwap(pool, alice, true).
3. Alice calls router.exactInputSingle({pool: pool, ...}).
4. Router calls pool.swap(recipient, ...) — msg.sender = router.
5. Pool calls _beforeSwap(router, ...).
6. Extension evaluates allowedSwapper[pool][router] == false → revert NotAllowedToSwap.
7. Alice's swap fails despite being explicitly allowlisted.

Failure B (bypass):
1. Pool admin calls setAllowedToSwap(pool, router, true).
2. Unprivileged user eve calls router.exactInputSingle({pool: pool, ...}).
3. Extension evaluates allowedSwapper[pool][router] == true → passes.
4. Eve trades on the curated pool with no individual authorization.
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
