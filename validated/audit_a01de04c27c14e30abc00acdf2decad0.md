Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of actual user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the pool's `swap()` call. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router address, not the actual user. If a pool admin allowlists the router to enable router-mediated swaps for curated users, every unprivileged caller can bypass the per-user allowlist by calling the router, rendering the allowlist completely ineffective for the primary public swap path.

## Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // router address when called via router
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to every configured extension via `abi.encodeCall`:

```solidity
// ExtensionCalling.sol L162-176
abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)   // sender = router address
)
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

`MetricOmmSimpleRouter.exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`) all call `pool.swap()` directly with no mechanism to forward the original caller's identity:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
    params.recipient,
    params.zeroForOne,
    ...,
    params.extensionData
);
// msg.sender in pool.swap() = router address
```

The `DepositAllowlistExtension` does not share this flaw because it ignores `sender` and checks `owner` (the position owner passed explicitly by the caller), which the liquidity adder correctly threads through. The swap path has no equivalent explicit-user argument.

The existing `onlyPool` guard in `BaseMetricExtension` only ensures the extension is called by a registered pool — it does not validate which human actor initiated the swap.

## Impact Explanation

A pool admin deploying a curated pool with `SwapAllowlistExtension` cannot simultaneously allow allowlisted users to swap via the router and block non-allowlisted users. If the admin allowlists the router (`allowedSwapper[pool][router] = true`) to enable router-mediated swaps for their curated users, every address can bypass the per-user allowlist by calling `router.exactInputSingle` (or any other router entry point). The allowlist is rendered completely ineffective for the router path, which is the primary public swap interface. Non-allowlisted users trade in a pool designed to be restricted (e.g., KYC-gated, whitelist-only institutional pool), exposing LP funds to unauthorized counterparties and permanently breaking the pool's curation invariant for the router path. This constitutes a broken core pool functionality causing direct loss of LP funds and policy bypass on curated pools.

## Likelihood Explanation

The router is the primary public swap interface documented and expected by users. A pool admin who wants allowlisted users to be able to use the router must allowlist the router address; there is no other mechanism. Once the router is allowlisted, the bypass is trivially reachable by any unprivileged caller with zero additional preconditions. The admin has no way to detect the bypass from the allowlist configuration alone, because the router address appears as a legitimate allowlisted entry. The attack requires no special privileges, no flash loans, and no complex setup — any EOA can call `router.exactInputSingle`.

## Recommendation

Pass the original caller's identity through the swap path so the extension can gate the economically relevant actor.

**Option A (preferred):** Add a `payer` or `originator` field to the `beforeSwap` hook signature and have the router supply `msg.sender` (the actual user) explicitly, mirroring how `addLiquidity` threads `owner` separately from `sender`. This is consistent with the existing `DepositAllowlistExtension` pattern.

**Option B:** Have `SwapAllowlistExtension` require that `sender` is never a known router/intermediary, and instead require direct pool calls for allowlisted pools. Document this restriction clearly.

At minimum, the `SwapAllowlistExtension` NatSpec must warn that allowlisting any intermediary contract (router, multicall wrapper) opens the pool to all callers of that intermediary.

## Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension (beforeSwap order)
  admin calls: setAllowedToSwap(pool, router, true)   // to enable router swaps for user1
  admin calls: setAllowedToSwap(pool, user1, true)    // intended curated user

Attack:
  attacker (not in allowlist) calls:
    router.exactInputSingle({pool: pool, recipient: attacker, ...})

  Execution trace:
    router.exactInputSingle()
      → pool.swap(recipient=attacker, ...) [msg.sender = router]
        → _beforeSwap(sender=router, ...)
          → SwapAllowlistExtension.beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓  (no revert)
        → swap executes, attacker receives tokens

Result:
  attacker bypasses the per-user allowlist and swaps in a curated pool.
  allowedSwapper[pool][attacker] was never set to true.
```

Foundry test plan: deploy `MetricOmmPool` with `SwapAllowlistExtension` in `beforeSwap` order, allowlist the router address, then call `router.exactInputSingle` from an address not in the allowlist and assert the swap succeeds (no `NotAllowedToSwap` revert). Confirm the same call from the non-allowlisted address directly to `pool.swap()` reverts. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
