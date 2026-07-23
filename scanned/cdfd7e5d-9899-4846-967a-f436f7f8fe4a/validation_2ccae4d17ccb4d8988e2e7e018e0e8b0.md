The call chain is now fully traced. Here is the analysis:

**Call path:**

1. EOA calls `MetricOmmSimpleRouter.exactInputSingle` → `msg.sender` = EOA
2. Router calls `pool.swap(recipient, ...)` → inside the pool, `msg.sender` = **router**
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` at line 231 → `sender` = **router**
4. `ExtensionCalling._beforeSwap` encodes and forwards `sender` = router to the extension
5. `SwapAllowlistExtension.beforeSwap` receives: `msg.sender` = pool, `sender` = router
6. Line 37 check: `allowedSwapper[pool][router]` → **passes** if router is allowlisted

The `sender` parameter in `beforeSwap` is always the **direct caller of the pool** (`msg.sender` inside `MetricOmmPool.swap`), not the originating EOA. The extension has no access to the original end user. [1](#0-0) [2](#0-1) 

---

### Title
`SwapAllowlistExtension.beforeSwap` checks router identity instead of end-user identity, allowing any user to swap through an allowlisted router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` enforces `allowedSwapper[pool][sender]` where `sender` is `msg.sender` from the pool's perspective — the **direct caller of the pool**. When `MetricOmmSimpleRouter.exactInputSingle` is used, that direct caller is the router contract, not the originating EOA. Any pool that allowlists the router address instead of individual end users grants unrestricted swap access to every user of that router.

### Finding Description
`MetricOmmPool.swap` passes its own `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
    msg.sender,   // ← always the direct caller (router)
    recipient,
    ...
);
``` [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards this value unchanged as the `sender` ABI argument to every configured extension: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then evaluates:

```solidity
// line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
//                   ^^^^^^^^^^^                    ^^^^^^^^^^^  ^^^^^^
//                   pool address                   pool address router address
``` [4](#0-3) 

`MetricOmmSimpleRouter.exactInputSingle` never passes the originating EOA to the pool; it simply calls `pool.swap(params.recipient, ...)` directly: [5](#0-4) 

**Concrete bypass scenario:**
- Pool admin calls `setAllowedToSwap(pool, router, true)` — a natural configuration for a pool that wants to accept router-mediated swaps.
- Pool admin does **not** allowlist individual EOAs.
- Any non-allowlisted EOA calls `router.exactInputSingle(...)`.
- Inside the pool, `msg.sender` = router → `sender` = router → `allowedSwapper[pool][router]` = `true` → check passes.
- The swap executes despite the end user not being on the allowlist.

### Impact Explanation
The `SwapAllowlistExtension` is the sole mechanism for restricting swap access on a per-pool basis. Its bypass means the allowlist provides no protection against arbitrary end users when any allowlisted router is present. Pools deployed with this extension under the assumption that only approved counterparties can trade are silently open to all users. This constitutes broken core pool functionality (the access-control invariant the extension exists to enforce is violated).

### Likelihood Explanation
Allowlisting the router is the expected operational pattern for any pool that wants to accept swaps via the official periphery router. A pool admin who allowlists `MetricOmmSimpleRouter` and relies on the extension to restrict end users will unknowingly expose the pool to all router users. No privileged attacker capability is required beyond calling the public `exactInputSingle` entry point.

### Recommendation
The pool's `swap` function should accept an explicit `swapper` parameter representing the originating end user, or the router should encode the originating EOA into `extensionData` and the extension should decode and check it. Alternatively, the extension documentation must clearly state that `sender` is the direct pool caller (not the end user), so pool admins know they are allowlisting routers, not individuals.

### Proof of Concept
```solidity
// Foundry integration test sketch
function test_nonAllowlistedEOASwapsThroughAllowlistedRouter() public {
    // Setup: pool with SwapAllowlistExtension, only router is allowlisted
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), address(router), true);
    // endUser is NOT allowlisted
    assertFalse(swapAllowlist.isAllowedToSwap(address(pool), endUser));

    // endUser calls router — sender passed to extension = router, not endUser
    vm.prank(endUser);
    uint256 amountOut = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token1),
            recipient: endUser,
            amountIn: 1000,
            amountOutMinimum: 0,
            zeroForOne: false,
            priceLimitX64: type(uint128).max,
            deadline: block.timestamp + 1,
            extensionData: ""
        })
    );
    // Swap succeeds — allowlist bypass confirmed
    assertGt(amountOut, 0);
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
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
