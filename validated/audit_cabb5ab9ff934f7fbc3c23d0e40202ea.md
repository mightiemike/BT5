The code confirms all three structural facts the claim relies on:

1. `MetricOmmPool.swap()` passes `msg.sender` to `_beforeSwap` — confirmed at line 231.
2. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap(...)` directly, making `msg.sender` at the pool the router — confirmed at lines 72-80.
3. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is the router — confirmed at line 37.

The two mutually exclusive failure modes are real and the exploit path is fully unprivileged.

Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of originating user for router-mediated swaps, making per-user allowlist enforcement impossible — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`, `metric-core/contracts/MetricOmmPool.sol`)

## Summary
`MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap`, which forwards it to `SwapAllowlistExtension.beforeSwap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool boundary is the router contract, not the originating user. This makes per-user allowlist enforcement structurally impossible: adding the router to the allowlist grants unrestricted access to every caller, while omitting it blocks all router-mediated swaps including those from legitimately allowlisted users.

## Finding Description
In `MetricOmmPool.swap()`, `_beforeSwap` is invoked with `msg.sender` as the `sender` argument: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension: [2](#0-1) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, so `msg.sender` at the pool is the router contract: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` evaluates the allowlist against this `sender` (the router), not the originating user: [4](#0-3) 

This produces two irresolvable failure modes with no correct configuration path:

- **Mode A — Allowlisted users blocked through the router.** If the admin populates the allowlist with specific user addresses but does not add the router, `allowedSwapper[pool][router]` is `false` and every router-mediated swap reverts, even for legitimately allowlisted users. The standard periphery path is broken for the pool's intended participants.
- **Mode B — Disallowed users bypass the allowlist through the router.** If the admin adds the router to the allowlist to fix Mode A, `allowedSwapper[pool][router]` is `true` for every caller regardless of individual allowlist status. Any address — including explicitly blocked users — can bypass the curation policy by routing through `MetricOmmSimpleRouter`.

No existing guard resolves this: the pool has no mechanism to pass the originating user's address, and the extension has no fallback to decode it from `extensionData`.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC-verified addresses, specific market makers, or whitelisted institutions) loses that restriction entirely once the router is added to the allowlist. Any unprivileged user can execute swaps on the restricted pool by routing through `MetricOmmSimpleRouter`, receiving output tokens at the pool's oracle-anchored price. This constitutes broken core pool functionality with direct fund-flow consequences: disallowed parties can drain liquidity at pool prices, and allowlisted users cannot use the standard periphery path. This meets the "broken core pool functionality causing loss of funds or unusable swap flows" impact criterion.

## Likelihood Explanation
The trigger is fully unprivileged: any user can call `MetricOmmSimpleRouter.exactInputSingle()` or equivalent. The only precondition is that the pool admin has added the router to the allowlist — a natural and expected administrative action for any pool that intends to support router-mediated trading for its allowlisted users. The configuration path is realistic and the bypass requires no special permissions or non-standard behavior.

## Recommendation
The pool's `swap()` function should accept an explicit `originator` parameter that the router populates with `msg.sender` before calling the pool, and `ExtensionCalling._beforeSwap` should forward that value as `sender` to extensions instead of `msg.sender`. Alternatively, `SwapAllowlistExtension.beforeSwap` should decode the original user from `extensionData` when the immediate caller is a known periphery contract. The invariant that must hold: the address checked against the allowlist must be the address that economically controls the swap input, not the intermediate dispatcher.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured; add `userA` to the allowlist; do **not** add the router.
2. `userA` calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`. The router calls `pool.swap(recipient, ...)`. `msg.sender` at the pool is the router. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `false` → revert. `userA` cannot use the standard periphery path despite being allowlisted. (**Mode A**)
3. Admin adds the router to the allowlist to fix Mode A.
4. `userB` (not on the allowlist, explicitly blocked) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`. The router calls `pool.swap(...)`. `msg.sender` at the pool is the router. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds. `userB` receives pool output tokens despite being a blocked address. (**Mode B**)

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
