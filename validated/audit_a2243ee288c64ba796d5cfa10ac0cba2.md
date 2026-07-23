Audit Report

## Title
SwapAllowlistExtension gates the router address instead of the original user, allowing any user to bypass the swap allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

## Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument forwarded by the pool, which is always the pool's `msg.sender` — the router contract when a user routes through `MetricOmmSimpleRouter`. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every user including those explicitly excluded from the allowlist can bypass the gate by calling any `exact*` function on the public router. The original caller's identity is stored only in transient callback context for payment settlement and is never forwarded to the pool or extension.

## Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to extensions:**

`MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at lines 230–240. When the caller is `MetricOmmSimpleRouter`, `msg.sender` is the router's address.

**Step 2 — Extension receives the router address as `sender`:**

`ExtensionCalling._beforeSwap` (lines 149–177) encodes and forwards the `sender` value unchanged to every registered extension via `_callExtensionsInOrder`.

**Step 3 — Allowlist checks the router, not the original user:**

`SwapAllowlistExtension.beforeSwap` (line 37) evaluates:
```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender])
```
Here `msg.sender` is the pool and `sender` is the router. The check passes if `allowedSwapper[pool][router] == true`.

**Step 4 — Router never forwards the original caller:**

`MetricOmmSimpleRouter.exactInputSingle` (lines 71–80) stores the original `msg.sender` only in transient callback context (`_setNextCallbackContext`) for payment settlement, then calls `pool.swap(params.recipient, ...)` directly. The original user's address is never passed to the pool or extension.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension` to restrict swapping to a curated set.
2. Admin calls `setAllowedToSwap(pool, router, true)` so allowlisted users can trade via the router.
3. Non-allowlisted Alice calls `router.exactInputSingle(...)`.
4. Router calls `pool.swap()` — pool's `msg.sender` is the router.
5. Pool passes router address as `sender` to the extension.
6. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Alice receives output tokens; allowlist is fully bypassed.

**Existing guards are insufficient:** The only guard is `allowedSwapper[msg.sender][sender]` in the extension. There is no mechanism in the pool's `swap` signature or in the extension protocol to carry the economically relevant actor (the original user) separately from the settlement payer (`msg.sender` of `pool.swap`).

## Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swapping to a curated set of addresses loses that protection entirely for any user who routes through `MetricOmmSimpleRouter`. Non-allowlisted users can execute swaps against the pool's liquidity at oracle-derived prices. This constitutes broken core pool functionality (the allowlist gate) and direct exposure of LP funds to unrestricted trading, meeting the "Admin-boundary break" and "Broken core pool functionality causing loss of funds" impact criteria.

## Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address, which simultaneously opens the bypass to all users. The router is a deployed, public, permissionless contract requiring no special access or setup. The bypass is trivially reachable by any unprivileged user.

## Recommendation

The pool's `swap()` function should accept an explicit `swapper` parameter (the economically relevant actor) separate from `msg.sender` (the settlement payer), and pass that value as `sender` to extensions. The router would then populate `swapper` with its own `msg.sender` before calling the pool. Alternatively, `SwapAllowlistExtension.beforeSwap` could decode the original caller from `extensionData` if the router is required to encode and sign it — but this requires a trusted router assumption. The cleanest fix is adding a `swapper` field to the pool's `swap` signature.

## Proof of Concept

```solidity
// Pool is configured with SwapAllowlistExtension.
// Pool admin allowlists the router so that allowlisted users can trade via it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT on the per-user allowlist.
// Direct swap reverts:
vm.prank(alice);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(alice, true, 1000, 0, "", "");

// Alice bypasses the allowlist via the router:
vm.prank(alice);
// Succeeds — extension sees sender == address(router), which IS allowlisted.
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: alice,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Alice receives token1 output — allowlist bypassed.
```

**Key code references:**
- [1](#0-0) 
- [2](#0-1) 
- [3](#0-2) 
- [4](#0-3)

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
