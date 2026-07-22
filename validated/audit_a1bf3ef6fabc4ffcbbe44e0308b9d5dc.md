### Title
`SwapAllowlistExtension` checks the router address instead of the originating user, allowing any user to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[msg.sender][sender]`, where `sender` is the `msg.sender` at the pool level. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the router contract address, not the originating user. A pool admin who allowlists the router (to permit router-mediated swaps for legitimate users) inadvertently opens the allowlist to every user, because any caller can reach the pool through the public router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ..., extensionData)   // msg.sender = router
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router]  ← checked, NOT the originating user
```

In `MetricOmmPool.swap`, `msg.sender` (the direct caller) is forwarded as `sender` to every `beforeSwap` hook: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that same `sender` into the hook call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router calls `pool.swap()` directly without forwarding the originating user's address: [4](#0-3) 

**Two broken invariants result:**

1. **Allowlist bypass (high impact):** A pool admin who allowlists the router address (so that legitimate users can swap through the standard periphery path) simultaneously grants every user on the network the ability to swap, because any caller can invoke `MetricOmmSimpleRouter.exactInputSingle()` and the extension will see `sender = router` (allowlisted) regardless of who the originating user is.

2. **Broken core functionality (medium impact):** A pool admin who allowlists individual user addresses (the intended design) will find that those users cannot swap through the router at all, because the extension checks `allowedSwapper[pool][router]` which is `false`. The only working path is a direct `pool.swap()` call, which requires the user to implement `IMetricOmmSwapCallback` themselves.

This is structurally identical to the Float Capital `latestMarket` bug: a stored/derived identifier (`sender` = the router, a wrong intermediary address) is used in place of the economically relevant identifier (the originating user), causing the guard to be applied to the wrong actor.

---

### Impact Explanation

On any pool with `SwapAllowlistExtension` configured:

- If the router is allowlisted: the allowlist is completely bypassed. Any unprivileged user can call `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutputSingle/exactOutput` and swap on a pool that was supposed to be restricted to a curated set of addresses. This is a direct policy failure on curated pools (e.g., KYC-gated, institutional-only, or compliance-restricted pools).
- If the router is not allowlisted: allowlisted users cannot use the standard periphery path, breaking the core swap flow for the intended participants.

Both outcomes represent a broken core pool functionality with direct fund-impacting consequences (unauthorized swaps drain LP value from curated pools; legitimate users are locked out of the standard swap path).

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public swap entry point in the periphery; most users will route through it rather than calling `pool.swap()` directly.
- A pool admin configuring `SwapAllowlistExtension` will naturally need to decide whether to allowlist the router. Either choice produces a broken outcome.
- No special privileges, flash loans, or unusual token behavior are required. Any user with a token balance can trigger the bypass.

---

### Recommendation

The extension must gate the **originating user**, not the intermediate router. Two approaches:

1. **Pass the originating user in `extensionData`:** The router encodes `msg.sender` (the originating user) into `extensionData` before calling `pool.swap()`. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `sender` only for direct pool calls; require extensions to use a dedicated "original sender" field:** Add an `originalSender` parameter to the `beforeSwap` hook interface that the pool populates from a transient-storage context set by the router, analogous to how the router already stores payer context in transient storage for callbacks.

3. **Document that `SwapAllowlistExtension` is incompatible with router-mediated swaps** and enforce this at the factory level (e.g., reject pool deployments that configure both a router-compatible setup and `SwapAllowlistExtension` without an explicit override).

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
// Router is NOT allowlisted → allowedUser cannot swap through router (broken functionality)

// OR: admin allowlists the router so allowedUser can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Now ANY user can bypass the allowlist:
address attacker = makeAddr("attacker");
deal(address(token0), attacker, 1e18);
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);
// This succeeds — extension sees sender=router (allowlisted), not attacker
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    tokenIn: address(token0),
    zeroForOne: true,
    amountIn: 1e18,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    deadline: block.timestamp,
    extensionData: ""
}));
// attacker successfully swapped on a pool that should have blocked them
``` [3](#0-2) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-240)
```text
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
