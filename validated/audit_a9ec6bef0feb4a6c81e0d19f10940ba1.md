Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual swapper, allowing allowlist bypass via the public router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` at the pool level. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the end user. If the router is allowlisted (required for any router-mediated swap to succeed), the allowlist gate is completely bypassed for every user who routes through the public router.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this `sender` value directly to every extension hook: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is the pool's caller. The `recipient` parameter (second argument) is explicitly unnamed and never inspected: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)`, making the router contract `msg.sender` at the pool level, while the actual user is only present as `params.recipient`: [4](#0-3) 

The same pattern applies to `exactInput` (intermediate hops use `address(this)` as recipient) and `exactOutputSingle`/`exactOutput`. In the `_exactOutputIterateCallback` path, the recursive hop calls `pool.swap(msg.sender, ...)` where `msg.sender` is the prior pool — again the router, not the user: [5](#0-4) 

The identity mismatch is: `sender` checked by the extension = router; actual economic actor = end user (available as `recipient` but ignored). Once the router is allowlisted, `allowedSwapper[pool][router] == true` passes for every caller regardless of who they are.

## Impact Explanation
A pool deployer configures `SwapAllowlistExtension` to restrict swaps to a curated set of counterparties (e.g., KYC'd institutions, whitelisted market makers). The allowlist is the sole access-control layer for swaps. Any unpermissioned user bypasses it by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The pool's LP assets are exposed to unrestricted swap flow, breaking the invariant that only allowlisted addresses can trade against the pool's liquidity. This constitutes broken core pool functionality with direct LP asset exposure — the exact wrong value is `allowedSwapper[pool][attacker]`, which is never checked; instead `allowedSwapper[pool][router]` is checked and passes.

## Likelihood Explanation
The bypass requires only that the router is allowlisted — a configuration any pool admin who wants to support router-mediated swaps for their allowlisted users must make. The router (`MetricOmmSimpleRouter`) is a public, permissionless contract. No privileged access, no special token, and no malicious setup is required. Any ordinary EOA can execute the bypass in a single transaction.

## Recommendation
The extension should gate the actual economic actor, not the intermediary. Two options:

1. **Check `recipient` instead of `sender`** — when the router calls `pool.swap`, it passes the real user as `recipient`. The extension already receives `recipient` as a parameter (currently unnamed/ignored). Note: `recipient` is user-controlled and could be any address, so this requires confirming the intended semantics across all call paths.
2. **Require the actual user identity in `extensionData`** — the router already forwards `extensionData` unmodified; the extension can decode a user-supplied identity and verify it against `msg.sender` of the router call (passed via `extensionData` by the router).

## Proof of Concept
```
1. Deploy pool with SwapAllowlistExtension.
2. Admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps for allowlisted users
3. Admin does NOT call setAllowedToSwap(pool, attacker, true).
4. Attacker calls:
       router.exactInputSingle({pool: pool, recipient: attacker, ...})
5. Router calls pool.swap(recipient=attacker, ...) — router is msg.sender at pool.
6. Pool calls _beforeSwap(sender=router, recipient=attacker, ...).
7. SwapAllowlistExtension checks allowedSwapper[pool][router] → true → passes.
8. Attacker's swap executes against the restricted pool's liquidity.
   allowedSwapper[pool][attacker] was never checked.
```

Foundry test: deploy pool with `SwapAllowlistExtension`, allowlist only the router, assert that an unallowlisted EOA calling `exactInputSingle` succeeds (swap executes) while a direct `pool.swap` call from the same EOA reverts with `NotAllowedToSwap`.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L220-228)
```text
    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );
```
