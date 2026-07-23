### Title
`SwapAllowlistExtension.beforeSwap` Gates the Router Address Instead of the End-User, Allowing Any Caller to Bypass a Curated Pool's Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` is designed to restrict swaps on curated pools to a specific set of approved addresses. However, the `beforeSwap` hook checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call — the immediate caller. When a user routes through `MetricOmmSimpleRouter`, the router becomes that immediate caller. If the router is allowlisted (the natural production configuration), every user who routes through it bypasses the allowlist entirely, regardless of whether they are individually approved.

---

### Finding Description

**Actor binding mismatch in `SwapAllowlistExtension.beforeSwap`**

`MetricOmmPool.swap()` calls `_beforeSwap` with `msg.sender` as the `sender` argument:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap()
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

`ExtensionCalling._beforeSwap` forwards this value unchanged to the extension:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
  BEFORE_SWAP_ORDER,
  abi.encodeCall(
    IMetricOmmExtensions.beforeSwap,
    (sender, recipient, ...)  // sender = msg.sender of pool.swap()
  )
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` is allowlisted:

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
  revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol L72-80
IMetricOmmPoolActions(params.pool).swap(
  params.recipient,
  params.zeroForOne,
  MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
  priceLimitX64,
  "",
  params.extensionData
);
```

The router is `msg.sender` to the pool, so `sender` in the extension = **router address**, not the actual end-user. If the pool admin allowlists the router (the expected production setup to enable router-mediated swaps for approved users), the allowlist check passes for **every user** who routes through it, regardless of whether they are individually approved.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` directly with the router as `msg.sender`.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelisted market makers). The admin allowlists the router so that approved users can trade conveniently through it. Any non-approved user can then call `MetricOmmSimpleRouter.exactInputSingle()` targeting that pool and trade freely, completely defeating the curation policy. The pool's LP assets are exposed to unrestricted swap flow, and any value-extraction or front-running protection the allowlist was meant to provide is nullified.

This is a direct loss-of-policy impact: the pool's core access control is broken for every swap that enters through the router, which is the primary supported public entry point.

---

### Likelihood Explanation

The trigger requires only that the pool admin has allowlisted the router — a natural and expected configuration for any production pool that wants to support router-mediated swaps for its approved users. The router is a public, permissionless contract. No privileged access, special tokens, or malicious setup is required. Any user who knows the pool address can call `exactInputSingle` and bypass the allowlist immediately.

---

### Recommendation

The `beforeSwap` hook must gate the **economic actor** — the end-user — not the immediate caller. Two options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` and the extension decodes and checks it. This requires a coordinated convention between router and extension.

2. **Check `recipient` instead of `sender`**: If the pool's design guarantees that the recipient is always the economic beneficiary, the extension could check `recipient`. However, this is not always true (e.g., `exactOutput` uses the pool itself as an intermediate recipient).

3. **Preferred fix**: The pool's `swap()` interface should expose the originating user as a distinct parameter (separate from `msg.sender`), and the extension should check that field. Alternatively, the router should not be allowlisted at the pool level; instead, the router itself should enforce the allowlist before calling the pool.

---

### Proof of Concept

```solidity
// Setup: pool admin deploys pool with SwapAllowlistExtension
// Admin allowlists the router (so approved users can trade via router)
extension.setAllowedToSwap(address(pool), address(router), true);

// Approved user: alice (allowlisted individually)
extension.setAllowedToSwap(address(pool), alice, true);

// Non-approved user: attacker (NOT allowlisted)
// Direct swap reverts:
vm.prank(attacker);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(attacker, true, 1000, type(uint128).max, "", "");

// Router-mediated swap succeeds — allowlist bypassed:
vm.prank(attacker);
router.exactInputSingle(
  IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    recipient: attacker,
    tokenIn: token0,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    extensionData: ""
  })
);
// Swap executes: sender seen by extension = router (allowlisted), not attacker
```

**Root cause**: `SwapAllowlistExtension.beforeSwap` checks `sender` (the router) rather than the originating user. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
