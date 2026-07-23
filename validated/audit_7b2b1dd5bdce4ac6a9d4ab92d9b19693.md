Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of end-user, allowing any caller to bypass per-user swap access control via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` argument, which is `msg.sender` of the pool's `swap()` call. When `MetricOmmSimpleRouter` intermediates a swap, that `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (the only way to permit any router-mediated swap for legitimate users), every unprivileged address can bypass the per-user gate by routing through the router.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` directly as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks the allowlist keyed on that `sender` (with `msg.sender` being the pool): [3](#0-2) 

When `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()`, the pool's `msg.sender` is the router contract — the original end-user `msg.sender` is stored only in the callback context for payment purposes, never threaded into `extensionData` or the swap call itself: [4](#0-3) 

The extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`. The `extensionData` bytes parameter in `beforeSwap` is received but entirely ignored by the extension, so there is no existing code path that threads the original caller's identity through to the allowlist check. [5](#0-4) 

## Impact Explanation
A pool deploying `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC-verified counterparties, institutional LPs, or whitelisted market makers) cannot enforce that restriction for any user who routes through `MetricOmmSimpleRouter`. Once the admin allowlists the router to unblock legitimate users, every unprivileged address can execute swaps against the pool. This constitutes broken core pool functionality: the configured access-control guard does not protect the intended invariant, enabling unauthorized extraction of LP value at oracle-anchored prices and circumvention of regulatory or contractual access controls.

## Likelihood Explanation
The trigger requires the pool admin to allowlist the router — a natural, expected action for any pool that wants to support the standard periphery. The admin has no indication from the contract or documentation that doing so opens the gate to all users. The bypass is then reachable by any unprivileged address with no further preconditions, no special tokens, and no elevated role.

## Recommendation
Pass the original end-user identity through the call chain so the extension can gate on it:

1. **Router-side**: Have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` before calling `pool.swap()`, and have `SwapAllowlistExtension.beforeSwap` decode and check it (optionally gated on `sender == trustedRouter` to prevent spoofing).
2. **Pool-side**: Add an `originator` field to the `swap` call signature that the router populates with its own `msg.sender`, and pass it to extensions alongside `sender`.

Until fixed, pools requiring per-user swap access control must not allowlist the router and must instruct users to call `pool.swap()` directly.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension as beforeSwap hook.
  - Pool admin calls setAllowedToSwap(pool, alice, true)   // alice is the only intended swapper
  - Pool admin calls setAllowedToSwap(pool, router, true)  // required to let alice use the router

Attack:
  - bob (not allowlisted) calls router.exactInputSingle({pool: pool, ...})
  - Router calls pool.swap(recipient, ...) — pool's msg.sender = router
  - Pool calls _beforeSwap(sender=router, ...)
  - Extension checks allowedSwapper[pool][router] == true → passes
  - Bob's swap executes successfully despite not being allowlisted

Result:
  - Bob extracts tokens from the pool at oracle-anchored prices.
  - Alice's per-user allowlist entry is irrelevant for router-mediated swaps.
  - The SwapAllowlistExtension guard is silently bypassed.
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
