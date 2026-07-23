The call chain is fully traceable. Here is the definitive analysis:

**Exact flow:**

1. Non-allowlisted `user` calls `MetricOmmSimpleRouter.exactInputSingle()`
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — at this point `msg.sender` inside the pool = **router address**
3. Pool's `swap()` passes `msg.sender` (= router) as `sender` to `_beforeSwap`
4. `_beforeSwap` calls `SwapAllowlistExtension.beforeSwap(sender=router, ...)`
5. Inside `SwapAllowlistExtension.beforeSwap`, the check is:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

where `msg.sender` = pool, `sender` = router. If `allowedSwapper[pool][router]=true`, the check **passes unconditionally**, regardless of who the actual end user is.

The `sender` the extension sees is always the immediate caller of `pool.swap()` — the router — not the originating EOA. Any user who routes through an allowlisted router bypasses the per-pool swap allowlist entirely.

---

### Title
SwapAllowlistExtension checks the router address instead of the end user, allowing any unprivileged user to bypass the per-pool swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as the address that called `pool.swap()`. When a router intermediates the call, `sender` is the router, not the originating user. Allowlisting the router therefore grants swap access to every user who routes through it, defeating the allowlist invariant.

### Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged to `SwapAllowlistExtension.beforeSwap`: [2](#0-1) 

The extension then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, so the pool always sees the router as `msg.sender`, never the originating user: [4](#0-3) 

### Impact Explanation
Any user who calls `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) against a pool where `allowedSwapper[pool][router]=true` will have their swap approved, even if they are not individually allowlisted. The pool admin's intent — restricting swaps to a curated set of addresses — is silently nullified. Non-allowlisted users can drain pool token balances by executing swaps that should have been rejected.

### Likelihood Explanation
The scenario requires the pool admin to have allowlisted the router address. This is a natural and expected configuration: a pool operator who wants to allow router-based trading but restrict direct `pool.swap()` calls would do exactly this. The bypass is automatic and requires no special knowledge from the attacker beyond knowing the router is allowlisted.

### Recommendation
The extension must check the **originating user**, not the immediate caller of `pool.swap()`. Two options:

1. **Pass the originating user explicitly**: Add an `originator` field to `extensionData` that the router populates with `msg.sender`, and have the extension read and verify it. This requires a trusted router convention.
2. **Check `recipient` instead of `sender`**: If the pool's design guarantees `recipient` is the end user, gate on `allowedSwapper[pool][recipient]`. However, `recipient` can be an arbitrary address, so this is only safe if the pool enforces `recipient == msg.sender` at the router level.
3. **Disallow router allowlisting**: Document that `allowedSwapper` entries must be EOAs or contracts that do not forward calls on behalf of arbitrary users, and enforce this off-chain.

The cleanest fix is option 1: the router sets `extensionData` to `abi.encode(msg.sender)` and the extension decodes and checks it, trusting only calls that arrive via a known router.

### Proof of Concept
```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Setup:
// 1. Deploy pool with SwapAllowlistExtension
// 2. allowedSwapper[pool][router] = true  (router is allowlisted)
// 3. allowedSwapper[pool][user]   = false (user is NOT allowlisted)

function test_bypassAllowlistViaRouter() public {
    // allowlist the router, not the user
    swapExtension.setAllowedToSwap(address(pool), address(router), true);
    assertFalse(swapExtension.isAllowedToSwap(address(pool), user));

    uint256 balBefore = token1.balanceOf(user);

    // non-allowlisted user swaps through the allowlisted router
    vm.prank(user);
    router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        tokenOut:        address(token1),
        zeroForOne:      true,
        amountIn:        1_000,
        amountOutMinimum: 0,
        recipient:       user,
        deadline:        block.timestamp + 1,
        priceLimitX64:   0,
        extensionData:   ""
    }));

    // swap succeeded despite user not being allowlisted
    assertGt(token1.balanceOf(user), balBefore);
}
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
