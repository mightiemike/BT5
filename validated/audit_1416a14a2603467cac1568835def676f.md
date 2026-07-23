All code references check out. The call chain is fully verified:

1. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` — passing the direct pool caller as `sender`. [1](#0-0) 

2. `ExtensionCalling._beforeSwap()` forwards that `sender` verbatim to every extension hook. [2](#0-1) 

3. `SwapAllowlistExtension.beforeSwap()` gates on `allowedSwapper[msg.sender][sender]` — where `sender` is the direct pool caller, not the end user. [3](#0-2) 

4. `MetricOmmSimpleRouter.exactInputSingle()` calls `pool.swap(params.recipient, ...)` directly, so the pool sees `msg.sender = router`. [4](#0-3) 

5. `DepositAllowlistExtension.beforeAddLiquidity()` correctly checks `owner` (the actual position owner), not `sender`, confirming the asymmetry is real and not intentional design. [5](#0-4) 

---

Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of end user, enabling full per-user allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of `pool.swap()` — the direct pool caller. When swaps are routed through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router address (the natural step to enable router-based trading on a curated pool) inadvertently grants every user — including those not individually allowlisted — the ability to bypass the per-user swap gate.

## Finding Description
`MetricOmmPool.swap()` invokes `_beforeSwap(msg.sender, recipient, ...)`, passing the direct caller as `sender`. `ExtensionCalling._beforeSwap()` encodes and forwards this `sender` to every configured extension hook. `SwapAllowlistExtension.beforeSwap()` then evaluates `allowedSwapper[msg.sender][sender]` — where `msg.sender` is the pool and `sender` is the direct pool caller. When `MetricOmmSimpleRouter.exactInputSingle()` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap(params.recipient, ...)`, the pool records `msg.sender = router`, so `sender` forwarded to the extension is the router address for every user who routes through it. If the router is allowlisted, the check `allowedSwapper[pool][router] == true` passes for all callers regardless of their individual allowlist status. The `DepositAllowlistExtension` avoids this by checking `owner` (the actual position owner, always set to the real user), but the swap extension has no equivalent "real user" field and relies solely on `sender`, which collapses to the router for all router-originated swaps. No existing guard in the extension or pool prevents this collapse.

## Impact Explanation
A curated pool deploying `SwapAllowlistExtension` to restrict trading to approved addresses fails to enforce that restriction for any user who routes through `MetricOmmSimpleRouter`. Disallowed users can execute swaps on a pool explicitly configured to deny them access. This is a curation failure matching the "Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows" impact gate: the allowlist extension — the sole access-control mechanism for the pool — is completely nullified for all router-originated swaps, allowing unauthorized trades to execute and settle against pool liquidity.

## Likelihood Explanation
The trigger requires the pool admin to allowlist the router address, which is the natural, expected configuration step for any admin who wants approved users to trade via the standard periphery rather than calling the pool directly. The admin has no on-chain signal that allowlisting the router is semantically equivalent to disabling the per-user allowlist entirely. The design asymmetry between `DepositAllowlistExtension` (checks `owner`) and `SwapAllowlistExtension` (checks `sender`) makes the mistake non-obvious. Once the router is allowlisted, any unprivileged address can exploit the bypass repeatably with no additional preconditions. Likelihood is Medium.

## Recommendation
Do not rely on `sender` (the direct pool caller) as the identity to gate in `SwapAllowlistExtension`. Two viable fixes:
1. **Check `recipient` instead of `sender`**: the recipient is the address that economically benefits from the swap and is always set by the originating user, even through the router. Replace `allowedSwapper[msg.sender][sender]` with `allowedSwapper[msg.sender][recipient]` (the second parameter of `beforeSwap`).
2. **Require the router to embed the real user in `extensionData`** and have the extension decode and check that address when `sender` is a known router, with a registry of trusted routers maintained by the factory or extension admin.

Additionally, document explicitly that allowlisting any intermediary contract (router, multicall, etc.) is equivalent to opening the pool to all users of that intermediary.

## Proof of Concept
```
Setup:
  pool admin deploys pool with SwapAllowlistExtension
  pool admin calls setAllowedToSwap(pool, router, true)   // to let approved users use the router
  pool admin calls setAllowedToSwap(pool, alice, true)    // alice is individually approved
  bob is NOT individually approved

Attack:
  bob calls router.exactInputSingle({pool: curatedPool, recipient: bob, ...})
    → router calls pool.swap(bob /*recipient*/, ...)
    → pool calls _beforeSwap(router /*msg.sender*/, bob /*recipient*/, ...)
    → extension.beforeSwap(router /*sender*/, bob, ...) is called
    → extension checks allowedSwapper[pool][router] == true  ✓
    → swap executes for bob despite bob not being allowlisted

Foundry test outline:
  1. Deploy SwapAllowlistExtension and a pool configured with it.
  2. Call setAllowedToSwap(pool, address(router), true).
  3. From address(bob) — not individually allowlisted — call router.exactInputSingle targeting the pool.
  4. Assert the swap succeeds (no NotAllowedToSwap revert).
  5. Confirm that a direct pool.swap() call from bob (without the router) reverts with NotAllowedToSwap.
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
