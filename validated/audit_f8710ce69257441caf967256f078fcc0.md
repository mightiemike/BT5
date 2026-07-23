Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of End User, Allowing Any User to Bypass the Swap Allowlist on Curated Pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. Any pool admin who allowlists the router to permit router-mediated swaps inadvertently opens the pool to every user on the network, completely voiding the curated-pool access control policy.

## Finding Description

**Step 1 — Pool passes `msg.sender` (the immediate caller) as `sender` to `_beforeSwap`:**

`MetricOmmPool.swap` at lines 230–231 calls:
```solidity
_beforeSwap(
  msg.sender,   // whoever called pool.swap()
  recipient,
  ...
);
``` [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension:**

`ExtensionCalling._beforeSwap` at lines 160–176 encodes and dispatches `sender` verbatim to each extension in `BEFORE_SWAP_ORDER`. [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`:**

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```
Here `msg.sender` is the pool (the caller of `beforeSwap`) and `sender` is whoever called `pool.swap()`. [3](#0-2) 

**Step 4 — `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router `msg.sender`:**

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```
The router is the immediate caller of `pool.swap()`, so `msg.sender` inside the pool is the router address, not the end user. [4](#0-3) 

The same pattern applies to `exactInput` (every hop), `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

**Result:** The extension evaluates `allowedSwapper[pool][router]` for every router-mediated swap, regardless of which end user initiated the transaction. Once the router is allowlisted (necessary for any router-mediated swap to work), the check passes for every caller of the router.

**Existing test gap:** `test_blocksSwapWhenSwapperNotAllowed` calls `_swap` which goes directly to the pool, not through the router, and therefore does not catch this bypass path. [6](#0-5) 

## Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and needs to support router-mediated swaps (the normal, documented use case) must call `setAllowedToSwap(pool, router, true)`. Once this is done, `allowedSwapper[pool][router]` returns `true` for every caller of the router — including addresses the admin explicitly never allowlisted. Any non-allowlisted user can bypass the curated-pool restriction by calling `MetricOmmSimpleRouter.exactInputSingle` instead of calling `pool.swap` directly. This is a direct, fund-impacting policy bypass: the pool was deployed with the intent to restrict trading to a specific set of counterparties, and that restriction is silently voided for all router users. This meets the "admin-boundary break: pool admin's access control policy bypassed by an unprivileged path" criterion.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint. Any pool using `SwapAllowlistExtension` that needs to support router-mediated swaps must allowlist the router, triggering the bypass automatically. No special privileges, flash loans, or unusual token behavior are required — a standard `exactInputSingle` call suffices. The bypass is repeatable by any address on the network.

## Recommendation

Pass the **originating user** rather than the immediate caller as the `sender` argument to extensions. One concrete approach: the router already stores the real user in transient storage via `_setNextCallbackContext` (the payer field). The pool can read this value back from transient storage and supply it as `sender` to extensions when the immediate `msg.sender` is a known router. Alternatively, redesign `SwapAllowlistExtension` to accept an explicit originator field, or have the pool propagate the true originator through the hook arguments rather than using raw `msg.sender`.

## Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured as a `beforeSwap` hook.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` — necessary to allow any router-mediated swap.
3. Non-allowlisted address `attacker` calls `MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})`.
4. Router calls `pool.swap(recipient, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. `attacker` successfully swaps on a pool that was supposed to block them.

A Foundry test reproducing this: deploy the pool as in `FullMetricExtension.t.sol`, deploy `MetricOmmSimpleRouter`, call `setAllowedToSwap(pool, router, true)`, then call `router.exactInputSingle` from an address not in the allowlist and observe the swap succeeds instead of reverting with `NotAllowedToSwap`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-232)
```text
    _beforeSwap(
      msg.sender,
      recipient,
```

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
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
  }
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

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L55-61)
```text
  function test_blocksSwapWhenSwapperNotAllowed() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);

    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
