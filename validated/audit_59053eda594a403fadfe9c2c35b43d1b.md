Audit Report

## Title
`SwapAllowlistExtension` checks router address as swapper identity, allowing any user to bypass the per-pool swap allowlist via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded from the pool, which is the immediate `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is the direct caller of `pool.swap()`, so the extension evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][originalEOA]`. A pool admin who allowlists the router to support router-mediated swaps for curated users inadvertently opens the pool to every user who can call the router, defeating the per-user access control entirely.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other entry points: `exactInput`, `exactOutputSingle`, `exactOutput`) calls `IMetricOmmPoolActions(params.pool).swap(...)` directly, making the router contract itself the `msg.sender` from the pool's perspective: [4](#0-3) [5](#0-4) 

Therefore, when any user calls the router, the extension evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][originalEOA]`. If the admin has allowlisted the router (a natural configuration to support router-mediated swaps for their curated users), the check passes for every caller regardless of individual allowlist status.

The `DepositAllowlistExtension` does not share this flaw because it gates on `owner`, which is the explicit position holder passed by the caller — the economically correct actor — rather than the intermediary contract: [6](#0-5) 

## Impact Explanation
A curated pool (KYC-gated, institution-only, or regulatory-restricted) that configures `SwapAllowlistExtension` and allowlists the public router loses its access control entirely. Any unprivileged user can execute swaps against the pool's LP liquidity, causing direct LP fund loss through unauthorized swaps at oracle-quoted prices, and breaking the core pool invariant that only approved counterparties trade against LP capital. This matches the allowed impact gate: broken core pool functionality causing loss of funds and admin-boundary break where an unprivileged path bypasses a configured guard.

## Likelihood Explanation
Medium. The precondition is that the pool admin allowlists the router address. This is a natural and expected configuration — the router is the protocol's own periphery contract and the primary user-facing entry point. A pool admin who wants to support router-mediated swaps for their allowlisted users has no way to do so without also opening the pool to all router users. The extension's design makes the correct configuration unreachable, so the bypass is reachable through a reasonable admin action, not a malicious one.

## Recommendation
Pass the original transaction initiator (`tx.origin`) or require the router to forward the originating user address through `extensionData` so the extension can check the real actor. A cleaner fix is to have the router encode the original caller in a standardized field of `extensionData`, have the extension decode it when present, and have the pool verify the router's identity before trusting the forwarded address. Alternatively, document that `SwapAllowlistExtension` is incompatible with router-mediated flows and enforce this at the factory level by rejecting pools that configure both a swap allowlist and a non-zero router address.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension configured on beforeSwap.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (intending to allow router-mediated swaps for allowlisted users).
  - Pool admin does NOT call setAllowedToSwap(pool, attacker, true).

Attack:
  1. Attacker (not individually allowlisted) calls
     MetricOmmSimpleRouter.exactInputSingle(..., pool, ...).
  2. Router calls pool.swap(recipient, ...) with msg.sender = router.
  3. Pool calls _beforeSwap(router, recipient, ...).
  4. Extension evaluates allowedSwapper[pool][router] → true → passes.
  5. Swap executes. Attacker receives output tokens from LP liquidity.

Result:
  - Attacker swapped on a pool they are not individually allowlisted for.
  - LP funds were transferred to an unauthorized counterparty.
  - The swap allowlist guard was bypassed through the public router path.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L104-112)
```text
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
