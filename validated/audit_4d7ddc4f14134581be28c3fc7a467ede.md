Audit Report

## Title
`SwapAllowlistExtension` gates on router address instead of end-user, allowing full allowlist bypass via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end-user. Any pool admin who allowlists the router (the only way to let allowlisted users use the router) simultaneously grants every non-allowlisted user the ability to bypass the swap gate by routing through the same router address.

## Finding Description
`MetricOmmPool.swap` invokes `_beforeSwap(msg.sender, recipient, ...)`, passing the immediate caller as `sender`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged as the first argument to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(params.recipient, ...)` directly, making the pool see `msg.sender` = router. The original end-user's address is stored only in transient storage for the payment callback and is never forwarded to the pool or extension: [4](#0-3) 

The result: the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][bob]`. If the router is allowlisted (required for any allowlisted user to use it), every non-allowlisted user passes the check by routing through the same router.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` (the intermediary) and checks `owner` (the actual position beneficiary): [5](#0-4) 

`SwapAllowlistExtension` has no equivalent correct binding — `recipient` (the address that actually receives output tokens) is available as the second parameter but is silently discarded.

## Impact Explanation
A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is fully bypassed by any unprivileged user who routes through `MetricOmmSimpleRouter`. The bypassing user executes a live swap against pool liquidity at oracle prices and receives real output tokens. This is a direct, fund-impacting bypass of a core pool access-control guard — broken core pool functionality causing loss of funds and unusable access-control flows.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard user-facing entry point deployed alongside the pool. Any pool admin who wants allowlisted users to be able to use the router must call `setAllowedToSwap(pool, address(router), true)`. This is the natural, expected configuration. Once the router is allowlisted, the bypass is reachable by any address on any allowlisted pool that supports router usage, requiring no special privileges or unusual conditions.

## Recommendation
The extension must gate the economically relevant actor, not the intermediary. Two sound approaches:

1. **Check `recipient` instead of `sender`**: Gate on who receives the output tokens, analogous to how `DepositAllowlistExtension` checks `owner`. Change line 37 of `SwapAllowlistExtension.sol` to check `allowedSwapper[msg.sender][recipient]` (the second parameter, currently unnamed/discarded).
2. **Router encodes original `msg.sender` into `extensionData`**: Have the router pass the original caller in `extensionData` in a verifiable way, and have the extension decode and verify it. This requires a coordinated protocol-level convention.

The simplest correct fix mirroring `DepositAllowlistExtension`'s pattern is option 1: gate on `recipient`.

## Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension` configured as `BEFORE_SWAP_ORDER` extension.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — only alice is allowed.
3. Pool admin calls `setAllowedToSwap(pool, address(router), true)` — router is allowlisted so alice can use it.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)` with himself as `recipient`.
5. Router calls `pool.swap(bob, ...)` with `msg.sender` = router.
6. Pool calls `_beforeSwap(router, bob, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. Bob receives output tokens despite never being allowlisted.

Direct call by Bob (`pool.swap(bob, ...)`) correctly reverts because `allowedSwapper[pool][bob]` = `false`. The router path silently substitutes the router's allowlisted identity for Bob's non-allowlisted one.

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
