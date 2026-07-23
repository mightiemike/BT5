### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Full Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument forwarded by the pool, which is `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the actual user. The extension therefore checks whether the **router** is allowlisted, not the actual swapper. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user on the network can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ..., extensionData)   // msg.sender = router
              → _beforeSwap(sender = router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender = router, ...)
                        → allowedSwapper[pool][router]  // checks router, not user
```

In `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap()` directly: [1](#0-0) 

The pool forwards `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` passes that `sender` value directly to the extension: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [4](#0-3) 

The actual user's address is stored only in transient storage for the payment callback and is never surfaced to the extension. The extension has no way to distinguish which end-user initiated the router call.

---

### Impact Explanation

The `SwapAllowlistExtension` is the protocol's mechanism for pool admins to restrict swap access to specific counterparties (e.g., KYC'd addresses, whitelisted market makers). The bypass completely defeats this control:

- If the pool admin **does not** allowlist the router, no user can swap through the router even if they are individually allowlisted — breaking legitimate router-mediated access.
- If the pool admin **does** allowlist the router (the only way to enable router-mediated swaps for any user), every address on the network can swap in the restricted pool by routing through `MetricOmmSimpleRouter`.

An unauthorized swapper gaining access to a restricted pool can execute swaps that the pool admin explicitly intended to block, causing LP losses through adverse selection or violating compliance requirements that the allowlist was designed to enforce. This is a broken core security invariant with direct fund-impact potential.

---

### Likelihood Explanation

The trigger requires no special privilege. Any user who knows the pool uses `SwapAllowlistExtension` and that the router is allowlisted (or can probe this on-chain) can call `MetricOmmSimpleRouter.exactInputSingle` with the target pool. The router is a public, permissionless contract. The bypass is reachable on every swap through the standard periphery path.

---

### Recommendation

The extension must receive the **original end-user address**, not the intermediary router address. Two approaches:

1. **Pass the real initiator through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap()`, and the extension decodes and checks it. This requires a convention between the router and the extension.

2. **Check `sender` and `recipient` together, or use a dedicated field**: Redesign the `beforeSwap` hook signature to include a separate `originator` field that the pool populates from a trusted transient-storage slot set by the router before the call, similar to how the callback payer is tracked.

The simplest safe fix is option 1: the router always appends `abi.encode(msg.sender)` to `extensionData`, and `SwapAllowlistExtension` decodes and checks that address when the caller is a known router.

---

### Proof of Concept

```solidity
// Pool is deployed with SwapAllowlistExtension.
// Pool admin allowlists the router so that allowedUser can swap via router.
extension.setAllowedToSwap(pool, address(router), true);

// allowedUser swaps normally — works as expected.
vm.prank(allowedUser);
router.exactInputSingle(ExactInputSingleParams({pool: pool, ...}));

// bannedUser — NOT in the allowlist — routes through the same router.
// The extension sees sender = router (allowlisted), not bannedUser.
// The swap succeeds, bypassing the allowlist entirely.
vm.prank(bannedUser);
router.exactInputSingle(ExactInputSingleParams({pool: pool, ...})); // passes, no revert
``` [4](#0-3) [5](#0-4)

### Citations

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
