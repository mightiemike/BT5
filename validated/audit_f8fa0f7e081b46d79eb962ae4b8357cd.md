### Title
`SwapAllowlistExtension` checks the router address instead of the actual user, allowing any unprivileged caller to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. The pool always passes its own `msg.sender` as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. If the pool admin allowlists the router so that legitimate users can use the standard periphery, every non-allowlisted user can bypass the gate by routing through the same router.

---

### Finding Description

**Pool `swap()` passes `msg.sender` as `sender` to the extension:** [1](#0-0) 

```solidity
_beforeSwap(
    msg.sender,   // <-- whoever called pool.swap()
    recipient,
    ...
    extensionData
);
```

**`ExtensionCalling._beforeSwap` forwards that value unchanged as the `sender` argument:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`:** [3](#0-2) 

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
```

**`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the pool's `msg.sender`:** [4](#0-3) 

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The router stores the real user in transient callback context for payment purposes, but it never forwards the real user's identity to the pool's `swap()` call. The pool therefore sees `msg.sender = router`, and the extension checks `allowedSwapper[pool][router]`.

**Attack path:**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to KYC'd addresses.
2. Admin allowlists the router (`setAllowedToSwap(pool, router, true)`) so that legitimate users can use the standard periphery.
3. Any non-allowlisted user calls `router.exactInputSingle(pool, ...)`.
4. The router calls `pool.swap()` — the pool sees `msg.sender = router`.
5. The extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. The non-allowlisted user successfully trades on a pool that was supposed to exclude them.

The converse is equally broken: if the admin does **not** allowlist the router, then allowlisted users cannot use the router at all, breaking the standard periphery for legitimate participants.

---

### Impact Explanation

Direct policy bypass on curated pools. Any non-allowlisted user can trade on a pool that was configured to restrict access (e.g., KYC, institutional, or compliance-gated pools) simply by routing through the public `MetricOmmSimpleRouter`. The allowlist provides zero protection against router-mediated swaps once the router itself is allowlisted. This is a direct loss of curation integrity and, depending on the pool's purpose, can expose restricted liquidity to unauthorized counterparties.

---

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any user who wants to bypass the allowlist only needs to call `exactInputSingle` or `exactInput` on the public router — no special privileges, no flash loans, no multi-transaction setup. The bypass is trivially reachable by any EOA or contract.

---

### Recommendation

The extension must gate by the **actual end user**, not by the intermediate caller. Two options:

1. **Pass the original caller through the router.** The router should forward `msg.sender` (the real user) as the `sender` argument to `pool.swap()` instead of relying on the pool to use its own `msg.sender`. This requires a protocol-level change to the swap interface or a dedicated "on-behalf-of" field.

2. **Check `sender` against the allowlist only when `sender` is not a trusted router.** The extension could maintain a registry of trusted routers and, when `sender` is a router, require the router to supply the real user identity in `extensionData`. This is more complex but avoids interface changes.

The simplest correct fix is option 1: the router should pass the real user's address as the `sender` to `pool.swap()`, and the pool interface should accept a separate `onBehalfOf` parameter that extensions can use for identity checks.

---

### Proof of Concept

```solidity
// Setup: curated pool with SwapAllowlistExtension
// Admin allowlists the router so legitimate users can use it
extension.setAllowedToSwap(address(pool), address(router), true);

// Alice is NOT allowlisted
assertFalse(extension.isAllowedToSwap(address(pool), alice));

// Alice calls pool.swap() directly → reverts with NotAllowedToSwap ✓
vm.prank(alice);
vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
pool.swap(alice, true, 1000, type(uint128).max, "", "");

// Alice calls router.exactInputSingle() → SUCCEEDS (bypass) ✗
// The extension sees sender = router (allowlisted), not alice
vm.prank(alice);
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool: address(pool),
    tokenIn: token0,
    tokenOut: token1,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    recipient: alice,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Alice receives token1 — allowlist bypassed
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-86)
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
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```
