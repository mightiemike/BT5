Audit Report

## Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Originating User, Allowing Any User to Bypass the Per-User Swap Allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is the router contract, not the originating EOA. A pool admin who allowlists the router to enable user-facing swaps inadvertently grants swap access to every user, completely defeating the per-user allowlist invariant and exposing LP funds to non-vetted counterparties.

## Finding Description
`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap(...)`. [1](#0-0) 

`MetricOmmPool.swap` populates this `sender` argument with `msg.sender` — the direct caller of `pool.swap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` then forwards this value as the first argument to the extension: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly. The originating user's address (`msg.sender` of `exactInputSingle`) is stored only in transient storage for the payment callback — it is never forwarded to the extension: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

The result is a binary broken state: if the admin does not allowlist the router, all router-mediated swaps revert (including those from allowlisted users); if the admin does allowlist the router, every user — regardless of allowlist status — can swap through the router.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates the economic actor (`owner`, explicitly passed as a separate argument) rather than the intermediary (`sender`): [6](#0-5) 

The `addLiquidity` call site demonstrates the correct pattern — `sender` (payer) and `owner` (economic actor) are separate arguments: [7](#0-6) 

The `swap` call site has no equivalent separation; only `sender` (the direct caller) and `recipient` (the output receiver) are available to extensions, and neither reliably identifies the originating user in a multi-hop or router-mediated path. [8](#0-7) 

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or institutional counterparties is fully bypassed by any user routing through `MetricOmmSimpleRouter`. The non-allowlisted user executes swaps at oracle-anchored prices against LP funds. LPs deposited under the invariant that only vetted counterparties could trade; the bypass constitutes a direct loss of LP principal and a broken core pool invariant (allowlist-gated swap access). This meets the "broken core pool functionality causing loss of funds" and "admin-boundary break bypassed by an unprivileged path" criteria.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary supported swap entrypoint for EOAs. Any pool admin who configures `SwapAllowlistExtension` and allowlists the router — the natural and necessary setup to enable user-facing swaps — triggers the bypass. The attacker requires no special privileges: they simply call `exactInputSingle` or `exactInput` on the router targeting the curated pool. The condition is reachable in normal production usage.

## Recommendation
The extension must gate the originating user, not the intermediary. The preferred fix mirrors the deposit extension pattern: add an explicit `originator` field to the swap call signature so the pool passes the true economic actor to extensions, analogous to how `owner` is passed separately from `sender` in `addLiquidity`. Alternatively, the router can encode the originating user's address in `extensionData`, and the extension can decode and verify it — though this requires a protocol-level convention and trust that the router populates it correctly.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (necessary to allow any router-mediated swap)
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(...) — from pool's perspective msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes successfully for the non-allowlisted attacker

Result:
  - attacker swaps against LP funds on a pool intended to be restricted
  - DepositAllowlistExtension correctly blocks attacker from adding liquidity
  - SwapAllowlistExtension silently passes because it sees the router, not the attacker
```

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

**File:** metric-core/contracts/MetricOmmPool.sol (L191-191)
```text
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-112)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
