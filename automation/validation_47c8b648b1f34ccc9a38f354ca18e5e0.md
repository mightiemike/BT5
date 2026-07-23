### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the end user, allowing any user to bypass the swap allowlist on curated pools - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router contract, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user — including explicitly disallowed ones — can bypass the allowlist by routing through the router.

---

### Finding Description

The call chain for a router-mediated swap is:

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(recipient, ..., extensionData)
              pool.swap: _beforeSwap(msg.sender, ...)   // msg.sender == router
                → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                     checks allowedSwapper[pool][router]
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `sender` (the router) against the per-pool allowlist: [3](#0-2) 

The allowlist is keyed `allowedSwapper[msg.sender][sender]` where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`. When the router is the caller, the check becomes `allowedSwapper[pool][router]`.

This creates an irreconcilable dilemma for the pool admin:

- **Router not allowlisted**: No router-mediated swap works, even for users who are individually allowlisted. Allowlisted users are forced to call `pool.swap()` directly.
- **Router allowlisted**: Every user — including explicitly blocked ones — can bypass the allowlist by routing through `MetricOmmSimpleRouter.exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput`.

The router stores the real user's identity only in transient callback context for payment purposes, not in any field forwarded to the pool's `swap()` call: [4](#0-3) 

There is no mechanism by which the pool or extension can recover the original end-user address from a router-mediated call.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners). Any disallowed user can bypass this restriction entirely by calling `MetricOmmSimpleRouter.exactInputSingle` with the target pool. The allowlist provides zero protection against router-mediated swaps once the router is allowlisted. This is a direct policy bypass on a core security control, allowing unauthorized users to trade on pools that were explicitly configured to exclude them.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported periphery path for swaps. Any pool admin who wants allowlisted users to use the router must allowlist the router itself, which immediately opens the bypass to all users. The exploit requires no special privileges, no unusual token behavior, and no multi-transaction setup — a single `exactInputSingle` call suffices.

---

### Recommendation

The `sender` argument passed to `beforeSwap` must represent the economic actor (the end user), not the intermediary contract. Two approaches:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes it. This requires a protocol-level convention and is fragile.

2. **Check `sender` in the extension but require the router to be excluded from the allowlist and instead pass the real user as `recipient` or via a dedicated field**: Not directly supported by the current interface.

3. **Preferred — fix at the pool/router level**: The pool should expose a `swapOnBehalf(address realUser, ...)` entry point, or the extension interface should include a dedicated `realSender` field distinct from the direct pool caller. Until then, the `SwapAllowlistExtension` should document that it cannot safely gate router-mediated swaps and pool admins must not allowlist the router while relying on per-user restrictions.

The minimal safe fix is to add a check in `beforeSwap` that reverts if `sender` is a known router/intermediary, forcing all allowlisted users to call `pool.swap()` directly:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    // sender is the direct pool.swap() caller; if it is a router, the real user is unknown
    require(!isKnownRouter[sender], RouterNotAllowed());
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted
extension.setAllowedToSwap(address(pool), alice, true);
// bob is NOT allowlisted

// Direct swap by bob — correctly reverts
vm.prank(bob);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(bob, true, 1000, 0, "", "");

// Pool admin must allowlist the router so alice can use it
extension.setAllowedToSwap(address(pool), address(router), true);

// Now bob bypasses the allowlist via the router
vm.prank(bob);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token0),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: bob,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// bob's swap succeeds — allowlist bypassed
```

The extension checks `allowedSwapper[pool][router]` (true) and passes, never inspecting bob's identity. [3](#0-2) [4](#0-3) [1](#0-0)

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
