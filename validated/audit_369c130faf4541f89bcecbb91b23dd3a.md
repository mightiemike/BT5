Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the actual user, enabling allowlist bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which `MetricOmmPool.swap` sets to `msg.sender` — the immediate caller of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the router contract, not the end user. The allowlist therefore gates the router address rather than the actual economic actor, producing both a direct bypass path and broken functionality for legitimately allowlisted users.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` directly: [4](#0-3) 

At that point `msg.sender` of `pool.swap()` is the router contract, so `sender` delivered to the extension is the router address, not the end user. The allowlist lookup `allowedSwapper[pool][router]` is evaluated instead of `allowedSwapper[pool][user]`.

This produces two concrete failure modes:

1. **Bypass**: A pool admin who allowlists the router to support normal router usage simultaneously grants every user — including those explicitly excluded — the ability to swap on the curated pool by routing through `MetricOmmSimpleRouter`.

2. **Broken functionality**: A pool admin allowlists specific user addresses but not the router. Those users cannot swap through the router because the router is not in the allowlist, even though the users themselves are.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` does not share this flaw: it checks the `owner` argument (the position recipient, i.e., the actual depositor), not `sender` (the intermediary): [5](#0-4) 

The `swap` function has no equivalent explicit "beneficiary" parameter, so the extension has no on-chain way to recover the true end user without additional data (e.g., `extensionData` or `recipient`).

## Impact Explanation
A curated pool's entire access-control boundary is defeated by routing through the publicly accessible `MetricOmmSimpleRouter`. Any user can execute swaps on a pool configured to restrict trading to a specific set of addresses. This is a direct policy bypass with fund-level consequences: unauthorized parties can drain liquidity from curated pools at oracle-derived prices, and the pool admin has no on-chain mechanism to prevent it without removing the router from the allowlist — which simultaneously breaks legitimate router users. This constitutes broken core pool functionality causing loss of funds and an unusable swap flow for the intended audience.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any user who routes through it triggers the bypass. No privileged access, special token, or unusual state is required. The only precondition is that the pool has `SwapAllowlistExtension` configured and the router is allowlisted — a natural admin action to support normal router usage. The bypass is therefore triggered by ordinary usage of the protocol.

## Recommendation
The extension must check the identity of the actual end user, not the intermediary. Two complementary fixes:

1. **Pass the originating caller through `extensionData`**: The router encodes `msg.sender` (the real user) into `extensionData` before calling `pool.swap()`. `SwapAllowlistExtension.beforeSwap` decodes and verifies this value. This requires a trust assumption that `sender` (the pool's `msg.sender`) is a known, trusted router, which can be enforced by also checking that `sender` is a registry-approved router address.

2. **Check `recipient` as a proxy for the user**: If the router always sets `recipient` to the actual user, the extension can gate on `recipient` instead of `sender`. This is simpler but requires the router's calling convention to be stable and documented.

Option 1 mirrors how `DepositAllowlistExtension` correctly gates on `owner` (the economic beneficiary) rather than `sender` (the intermediary).

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)   // enable router-mediated swaps
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker calls MetricOmmSimpleRouter.exactInputSingle(...)
  2. Router calls pool.swap(recipient=attacker, ..., extensionData=...)
  3. pool.swap sets sender = address(router), calls _beforeSwap(router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Swap executes; attacker receives output tokens.

Result: attacker, who is not on the allowlist, successfully swaps on a curated pool.
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
