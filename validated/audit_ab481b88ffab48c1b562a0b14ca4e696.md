Audit Report

## Title
`SwapAllowlistExtension` checks router address as `sender` instead of actual user, allowing full allowlist bypass via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the pool's `msg.sender` — the immediate caller of `MetricOmmPool.swap`. When any user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. If the pool admin allowlists the router (required for any allowlisted user to use the router), every user — including explicitly non-allowlisted ones — can bypass the restriction by calling any router entry point.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // ← pool's immediate caller, not the originating user
  recipient, ...
);
```

`ExtensionCalling._beforeSwap` forwards this value unchanged through `_callExtensionsInOrder` to every configured extension. `SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is whoever called the pool. When `MetricOmmSimpleRouter.exactInputSingle` is used, it calls the pool directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

This makes `sender = address(router)` from the extension's perspective. The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all call the pool directly. The actual `msg.sender` of the router call (the end user) is never passed to or checked by the extension.

**Exploit flow:**
1. Pool deployed with `SwapAllowlistExtension`; admin allowlists `alice` and the router (necessary for alice to use the router)
2. `bob` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`
3. Router calls `pool.swap(...)` → pool calls `_beforeSwap(msg.sender=router, ...)`
4. Extension checks `allowedSwapper[pool][router]` → `true` → swap succeeds
5. Bob's address is never consulted

## Impact Explanation
A pool configured with `SwapAllowlistExtension` is intended to restrict swaps to a specific set of addresses (KYC-verified counterparties, whitelisted market makers, institutional participants). The allowlist is the sole on-chain enforcement mechanism. Once the router is allowlisted — a necessary operational step for any allowlisted user to use the standard periphery — the restriction is completely nullified for all router callers. LP funds in a permissioned pool are exposed to unrestricted counterparties. The pool admin cannot simultaneously permit legitimate users to use the router and block unauthorized users; the two goals are mutually exclusive under the current design. This constitutes broken access-control with direct fund-exposure impact.

## Likelihood Explanation
`MetricOmmSimpleRouter` is the standard publicly deployed periphery. No special privileges, malicious setup, or non-standard tokens are required. The only precondition — the router being allowlisted — is the expected operational configuration whenever any allowlisted user needs to use the router. Any unprivileged user can trigger the bypass by calling any of the four router entry points.

## Recommendation
Pass the originating user through the call chain rather than the immediate `msg.sender`. The preferred approach mirrors the existing payer-slot pattern already used in the router: before calling the pool, store the real user address in a verified transient storage slot; have the extension (or pool) read that slot instead of relying on the raw `sender` argument. Alternatively, document and enforce that the router must never be allowlisted and that allowlisted users must call the pool directly — but this makes the router unusable for permissioned pools and is not a code-level fix.

## Proof of Concept
```solidity
// Setup:
// 1. Pool deployed with SwapAllowlistExtension
// 2. Admin: allowedSwapper[pool][alice] = true
// 3. Admin: allowedSwapper[pool][router] = true  (required for alice to use router)

// Attack: bob (not allowlisted) bypasses via router
vm.prank(bob);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        recipient: bob,
        zeroForOne: true,
        amountIn: 1_000e18,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp,
        extensionData: ""
    })
);
// Router calls pool.swap() → pool passes msg.sender=router to _beforeSwap
// Extension checks allowedSwapper[pool][router] = true → passes
// Bob's address is never checked
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
