The code confirms all three legs of the claim. Let me verify the exact call chain:

Audit Report

## Title
Swap Allowlist Checks Router Address Instead of Ultimate User, Allowing Any Router Caller to Bypass Per-User Swap Restrictions — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` parameter, which the pool sets to its own `msg.sender` — the direct caller of `pool.swap()`. When `MetricOmmSimpleRouter` intermediates, the pool's `msg.sender` is the router, so the extension evaluates `allowedSwapper[pool][router]`. A pool admin who allowlists the router to enable approved users to trade via the standard periphery entry point simultaneously grants every caller of that router unrestricted access to the curated pool, collapsing the per-user gate to a per-router gate.

## Finding Description

**Root cause — extension checks direct pool caller, not ultimate user:**

`SwapAllowlistExtension.beforeSwap` performs:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the contract invoking the extension). `sender` is the first argument forwarded by the pool, which `MetricOmmPool.swap` always sets to its own `msg.sender`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` then passes this value unchanged as the `sender` argument to every registered extension.

**Router call path:**

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...
    params.extensionData
);
```

Inside the pool, `msg.sender` is the router contract. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

**Bypass path:**

A pool admin who wants approved users (e.g., `userA`) to trade via the router must call `setAllowedToSwap(pool, router, true)`. There is no mechanism to allowlist the router only for specific end-users — `allowedSwapper` is a flat `pool → address → bool` mapping. Once the router is allowlisted, `allowedSwapper[pool][router] == true` passes for every caller of the router, including users who were never individually approved.

**Existing guards are insufficient:**

`setAllowAllSwappers` is a separate toggle that opens the pool to everyone; it does not help. There is no `extensionData` decoding in `beforeSwap` that could recover the original caller. The router does not encode the original `msg.sender` into `extensionData`.

## Impact Explanation

LP providers on KYC-gated or institutionally curated pools are exposed to swaps from non-approved counterparties. The pool admin's intent — restricting counterparty exposure to a vetted set of addresses — is entirely defeated. Any user of `MetricOmmSimpleRouter` can execute swaps against the pool. Because the pool prices swaps from the oracle, adversarial non-allowlisted users can trade at oracle-fair prices against LP positions that were explicitly designed to exclude them, constituting a broken core pool access-control invariant and direct exposure of LP principal to unapproved counterparties.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the standard periphery entry point for all swaps. Any pool admin who deploys `SwapAllowlistExtension` and wants approved users to trade via the router faces a binary choice: either allowlist the router (opening the pool to all router callers) or force approved users to call the pool directly (making the router unusable for that pool). The misconfiguration is a predictable operational outcome, not a rare edge case. No special attacker capability is required — any address can call `exactInputSingle` on the router.

## Recommendation

The extension must verify the ultimate user identity rather than the direct pool caller. The most robust fix is to have the router encode the original `msg.sender` into `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a known router address. Alternatively, document that pools using `SwapAllowlistExtension` must not allowlist the router address and that allowlisted users must call the pool directly — but this makes the router unusable for curated pools and should be enforced at the contract level, not by documentation alone.

## Proof of Concept

```
1. Deploy pool with SwapAllowlistExtension as beforeSwap hook.
2. Pool admin: setAllowedToSwap(pool, userA, true)
3. Pool admin: setAllowedToSwap(pool, router, true)
   — required so userA can trade via MetricOmmSimpleRouter.
4. Non-allowlisted userC calls:
     router.exactInputSingle({pool, tokenIn, tokenOut, amountIn, recipient: userC, ...})
   The router calls pool.swap(recipient=userC, ...).
   Inside the pool, msg.sender = router.
   Extension evaluates: allowedSwapper[pool][router] == true → passes.
5. userC's swap executes on the curated pool despite never being individually approved.
```

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
