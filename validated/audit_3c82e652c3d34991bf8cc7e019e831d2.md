Audit Report

## Title
SwapAllowlistExtension Checks Router Address Instead of Original User, Enabling Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` as seen by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the originating user. If a pool admin allowlists the router address (the natural action to let approved users use the standard periphery), any unprivileged user can bypass the curated pool's swap allowlist by calling the router, completely defeating the access control.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension via `abi.encodeCall`. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is the router — not the originating user.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly with no mechanism to forward the original caller:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
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

The same pattern applies to `exactInput` (L104-112), `exactOutputSingle` (L136-137), and `exactOutput` (L165-181) — all router entry points call `pool.swap()` with the router as `msg.sender`.

**Exploit flow:**
1. Pool admin deploys a pool with `SwapAllowlistExtension` to restrict swaps to approved addresses.
2. Admin calls `setAllowedToSwap(pool, address(router), true)` so that allowlisted users can use the standard periphery.
3. Unprivileged attacker (bob, not allowlisted) calls `router.exactInputSingle(...)` targeting the curated pool.
4. Pool calls `_beforeSwap(sender=router, ...)`.
5. Extension checks `allowedSwapper[pool][router] == true` → passes.
6. Bob's swap executes despite not being on the allowlist.

**Existing guards are insufficient:** The only guard is `allowedSwapper[msg.sender][sender]` in the extension. There is no mechanism in the pool or router to propagate the original `msg.sender` through the call chain. The `_setNextCallbackContext` in the router stores the original payer for payment purposes only — it is never forwarded to the pool's `swap()` call as the `sender`.

## Impact Explanation
A pool admin who deploys a curated pool with `SwapAllowlistExtension` and allowlists the router (a natural and expected action) inadvertently opens the pool to all users. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle` and pass the allowlist check, allowing unauthorized parties to swap against institutional-only or rate-limited liquidity. This constitutes a broken core pool access-control invariant: the extension's stated purpose — "Gates `swap` by swapper address, per pool" — is completely defeated via the router path. The impact is unauthorized swap execution against restricted LP positions, which can result in direct loss of LP value or extraction of favorable oracle-anchored prices by unauthorized parties.

## Likelihood Explanation
`MetricOmmSimpleRouter` is a public, permissionless contract. Any user can call it. A pool admin who wants allowlisted users to use the router has no alternative but to allowlist the router address — there is no other mechanism to enable router-mediated swaps for approved users. No privileged access, special token behavior, or off-chain manipulation is required. The attacker only needs to call `exactInputSingle` on the router targeting the curated pool.

## Recommendation
**Short term:** In `SwapAllowlistExtension.beforeSwap`, gate on `recipient` as a proxy for the economic actor, or require pools using `SwapAllowlistExtension` to be accessed only directly (not via router). Alternatively, document and enforce at the factory level that `SwapAllowlistExtension` is incompatible with router-mediated swaps.

**Long term:** Introduce an `originator` field in the swap hook arguments so extensions can gate on the true economic actor rather than the immediate `msg.sender` of the pool. The router should forward the original `msg.sender` through a dedicated parameter in `pool.swap()`.

## Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension; only alice is allowlisted.
// Admin allowlists the router so alice can use it:
extension.setAllowedToSwap(pool, address(router), true);

// Attacker (bob, not allowlisted) calls the router:
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: token0,
    recipient: bob,
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// Pool calls _beforeSwap(sender=router, ...)
// Extension checks allowedSwapper[pool][router] == true → passes
// Bob's swap executes despite not being on the allowlist.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
