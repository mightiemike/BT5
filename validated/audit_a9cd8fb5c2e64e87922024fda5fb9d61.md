Audit Report

## Title
`SwapAllowlistExtension` Gates on Router Address Instead of Actual User, Enabling Full Allowlist Bypass on Router-Mediated Swaps — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap()` checks the `sender` argument, which is `msg.sender` of `MetricOmmPool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the actual user. If the pool admin allowlists the router (the only way to let allowlisted users trade through the router), the check passes for every caller regardless of their allowlist status, completely defeating the access-control gate.

## Finding Description

**Step 1 — Pool passes its own `msg.sender` as `sender` to the extension.**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards this value verbatim as the `sender` argument to every configured extension: [2](#0-1) 

**Step 2 — `SwapAllowlistExtension` checks that `sender` value.**

The check `allowedSwapper[msg.sender][sender]` resolves to `allowedSwapper[pool][whoever_called_pool_swap]`: [3](#0-2) 

**Step 3 — The router calls `pool.swap()` directly, making itself the pool's `msg.sender`.**

In `exactInputSingle`, the router stores the real user in transient context but calls `pool.swap()` as itself: [4](#0-3) 

The actual user's address is stored only in the transient callback context for payment settlement — it is never forwarded to the pool as the swap initiator. The same applies to `exactInput` (line 103-112), `exactOutputSingle` (line 135-137), `exactOutput` (line 165-181), and intermediate hops in `_exactOutputIterateCallback`: [5](#0-4) 

**Step 4 — The allowlist check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.**

The pool admin faces an impossible choice:

| Router allowlisted? | Effect |
|---|---|
| Yes | Every user — including non-allowlisted ones — passes the check by routing through the router. Allowlist is fully bypassed. |
| No | Allowlisted users cannot use the router at all; they must call `pool.swap()` directly. |

**Contrast with `DepositAllowlistExtension`:** The deposit extension correctly gates on `owner` (the economically relevant actor) rather than `sender` (the intermediary), because `addLiquidity` takes an explicit `owner` parameter: [6](#0-5) 

`SwapAllowlistExtension` has no equivalent "real actor" parameter to check — `sender` is the only identity field passed, and it is the intermediary router, not the user.

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional traders, or protocol-controlled addresses) is fully open to any caller who routes through `MetricOmmSimpleRouter`. The allowlist provides zero protection on the router path. This is a direct bypass of a deployed access-control mechanism: unauthorized users can execute swaps on a pool designed to be restricted, draining liquidity at oracle-derived prices. This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" impact criteria at **High** severity.

## Likelihood Explanation

**Medium-High.** The pool admin must allowlist the router for any allowlisted user to trade through it — the standard UX path. This is the expected operational configuration; the admin has no reason to suspect it opens the gate to everyone. An attacker needs only to observe that the pool uses `SwapAllowlistExtension` and route through the public router — no special permissions, no flash loans, no multi-block setup required.

## Recommendation

`SwapAllowlistExtension.beforeSwap()` must gate on the original user, not the intermediary. Two complementary approaches:

1. **Pass the original initiator through the router.** The router already stores `msg.sender` in the transient callback context via `_setNextCallbackContext(..., msg.sender, ...)`. Extend the pool's `swap()` signature with an optional `initiator` parameter, or have the router encode the real user in `extensionData` and have the extension decode it.

2. **Alternatively, gate on `recipient` instead of `sender` for router paths**, since `recipient` is the address that receives the output tokens and is set by the actual user. However, this changes the semantics of the allowlist and may not cover all cases.

The cleanest fix is option 1: the router should forward the real user's address in a standardized field that the extension can verify, analogous to how `DepositAllowlistExtension` correctly gates on `owner` rather than `sender`.

## Proof of Concept

```
Setup:
  - Pool P configured with SwapAllowlistExtension E
  - Admin allowlists only Alice: allowedSwapper[P][Alice] = true
  - Admin also allowlists the router R so Alice can use it: allowedSwapper[P][R] = true

Attack (Bob, not allowlisted):
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, recipient: Bob, ...})
  2. Router stores Bob as payer in transient context via _setNextCallbackContext(P, CALLBACK_MODE_JUST_PAY, Bob, tokenIn)
  3. Router calls P.swap(Bob, zeroForOne, amountIn, priceLimitX64, "", extensionData)
  4. P.swap() sees msg.sender = Router R
  5. P calls _beforeSwap(sender=R, recipient=Bob, ...)
  6. SwapAllowlistExtension checks allowedSwapper[P][R] → true ✓
  7. Swap executes; Bob receives output tokens
  8. Allowlist was never consulted for Bob's address

Result: Bob, a non-allowlisted address, successfully swaps on a curated pool.

Foundry test sketch:
  - Deploy pool with SwapAllowlistExtension
  - setAllowedToSwap(pool, alice, true)
  - setAllowedToSwap(pool, router, true)
  - vm.prank(bob); router.exactInputSingle(...)
  - Assert swap succeeds (no NotAllowedToSwap revert)
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
