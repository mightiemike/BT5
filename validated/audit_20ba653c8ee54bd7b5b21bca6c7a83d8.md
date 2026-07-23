Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address instead of end-user identity, enabling universal allowlist bypass via `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument — which is `msg.sender` inside `MetricOmmPool.swap` — against the per-pool allowlist. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` inside the pool is the router contract, not the end user. A pool admin who allowlists the router to support standard periphery usage inadvertently grants swap access to every address on-chain, completely defeating the per-user allowlist.

## Finding Description

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards `sender` unchanged to all configured extensions: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the immediate caller of the pool: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap(...)` directly, making the router the `msg.sender` inside the pool. Critically, the router passes `params.extensionData` verbatim — it does **not** encode the actual caller's address into `extensionData`: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`. There is no existing guard that recovers the true end-user identity from the call context.

## Impact Explanation
The `SwapAllowlistExtension` is the sole access-control mechanism for curated pools. Once the router is allowlisted — the expected operational state for any pool that wants to support the standard periphery — any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle` and execute swaps against a pool intended to be restricted. This is a direct, unconditional allowlist bypass: the attacker needs no special role, no funds beyond swap input, and no precondition beyond the router being allowlisted. The broken invariant is `allowedSwapper[pool][actualUser]` being the authoritative gate; the actual gate becomes `allowedSwapper[pool][router]`, a single boolean that opens access to all users simultaneously.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the canonical swap entry point for the protocol. Any pool admin deploying a curated pool who also wants users to use the standard router will allowlist it. The mismatch between "allowlist the router" and "allow all users" is non-obvious, undocumented as a constraint, and has no on-chain warning. Every curated pool that has the router allowlisted is immediately and repeatably exploitable by any address with no additional setup.

## Recommendation
The extension must check the economically relevant actor — the end user — not the immediate pool caller. Two sound approaches:

1. **Router encodes caller into `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. The extension maintains a registry of trusted routers; for those callers it decodes and checks the embedded user address instead of `sender`.

2. **Dedicated `sender` override field in `extensionData`**: Define a convention where any trusted intermediary (router, multicall, etc.) encodes the true originator in a standard prefix of `extensionData`, and the extension verifies the intermediary is trusted before accepting the override.

The direct-call path (user calls pool directly) is already correct and requires no change.

## Proof of Concept
```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - allowedSwapper[pool][alice] = true   // alice is KYC'd
  - allowedSwapper[pool][router] = true  // admin enables router support
  - bob is NOT in allowedSwapper

Attack:
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(recipient, zeroForOne, amount, limit, "", params.extensionData)
     → msg.sender inside pool = router address
  3. pool._beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap: allowedSwapper[pool][router] == true → passes
  5. Swap executes for bob despite bob not being allowlisted

Result: bob bypasses the per-user allowlist and swaps on a curated pool.
```

Foundry test outline:
- Deploy `SwapAllowlistExtension`, configure pool with it
- `setAllowedToSwap(pool, alice, true)` and `setAllowedToSwap(pool, address(router), true)`
- Call `router.exactInputSingle(...)` from `bob` (not allowlisted)
- Assert swap succeeds (no `NotAllowedToSwap` revert)

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
