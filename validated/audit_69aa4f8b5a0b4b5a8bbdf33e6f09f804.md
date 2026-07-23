Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks the router address as `sender` instead of the actual end-user, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, so the extension evaluates the router's allowlist status rather than the actual end-user's. A pool admin who allowlists the router to support standard periphery tooling inadvertently grants every on-chain address unrestricted swap access, completely defeating the per-user curation the extension was deployed to enforce.

## Finding Description
The call path is confirmed by the production code:

**Step 1:** `MetricOmmPool.swap` passes `msg.sender` (the direct caller) as `sender` to `_beforeSwap`: [1](#0-0) 

**Step 2:** `ExtensionCalling._beforeSwap` forwards `sender` unchanged to every configured extension: [2](#0-1) 

**Step 3:** `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

**Step 4:** `MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no user identity forwarded, making the router the `msg.sender` of the pool call: [4](#0-3) 

The result is that `sender` arriving at the extension is `address(router)`, not the user's address. Two concrete failure modes arise:

1. **Allowlist bypass (high impact):** A pool admin who allowlists the router (`setAllowedToSwap(pool, router, true)`) to support standard tooling inadvertently grants every user on-chain access. Any non-allowlisted user can bypass the per-user gate by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the public router.

2. **Broken functionality for legitimate users (medium impact):** If the admin does *not* allowlist the router, every individually-allowlisted user who tries to swap through the router is blocked with `NotAllowedToSwap`. The only working path is a direct `pool.swap()` call, which requires the caller to implement `IMetricOmmSwapCallback` — an unreasonable burden for ordinary EOA users and incompatible with standard tooling.

`DepositAllowlistExtension` does not share this flaw because it gates on `owner` (the position owner passed explicitly by the pool), not on `sender` (the direct caller): [5](#0-4) 

## Impact Explanation
**High.** A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC'd or otherwise vetted addresses loses that guarantee entirely the moment the router is allowlisted. Any address on-chain can execute swaps against the pool's liquidity by routing through `MetricOmmSimpleRouter`. This constitutes a direct broken-core-functionality / allowlist-bypass impact: the pool's LP assets are exposed to unrestricted swap flow that the pool admin explicitly intended to prevent. This matches the allowed impact gate: "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" and "Admin-boundary break: pool admin exceeds caps, bypasses timelocks, or factory/oracle role checks are bypassed by an unprivileged path."

## Likelihood Explanation
**High.** `MetricOmmSimpleRouter` is the canonical periphery entry point for swaps. A pool admin who configures `SwapAllowlistExtension` and also wants to support standard tooling (wallets, aggregators, the protocol's own UI) will naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior — only a standard router call available to any EOA.

## Recommendation
The extension must check the economically responsible actor, not the immediate pool caller. The recommended fix mirrors the `DepositAllowlistExtension` design: the router should forward the original caller's address as an authenticated field (e.g., encoded in `extensionData` with a trusted-forwarder or signed-permit pattern), and `SwapAllowlistExtension.beforeSwap` should decode and verify that field rather than the raw `sender` argument. At minimum, documentation must warn that allowlisting the router grants unrestricted access to all users.

## Proof of Concept
```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists alice and the router (to support standard tooling)
swapExt.setAllowedToSwap(pool, alice, true);
swapExt.setAllowedToSwap(pool, address(router), true);

// Bob is NOT allowlisted
// Direct call reverts correctly:
vm.prank(bob);
pool.swap(...);  // reverts NotAllowedToSwap (sender=bob, not allowlisted)

// Router-mediated call succeeds — bypass:
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: bob,
        ...
    })
);
// pool.swap() called with msg.sender=router
// extension checks allowedSwapper[pool][router] → true
// Bob's swap executes despite not being allowlisted
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
