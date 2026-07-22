### Title
SwapAllowlistExtension Gates on Router Address Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router address**, not the end user. If the pool admin allowlists the router (the natural step to let their curated users access the standard periphery), every unprivileged user can bypass the allowlist by routing through the router.

---

### Finding Description

**Allowlist check — `SwapAllowlistExtension.beforeSwap`:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (the extension's caller). `sender` is the first argument forwarded by the pool.

**Pool sets `sender` to its own `msg.sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

**Router calls `pool.swap()` directly:**

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

When a user calls `router.exactInputSingle(...)`, the call chain is:

```
user → router.exactInputSingle()
         → pool.swap()          [msg.sender = router]
             → _beforeSwap(router, ...)
                 → extension.beforeSwap(sender=router, ...)
                     → allowedSwapper[pool][router]  ← checked, NOT the end user
```

The extension therefore checks whether the **router contract** is allowlisted, not whether the **end user** is allowlisted.

**Contrast with `DepositAllowlistExtension`**, which correctly checks `owner` (the position owner — the economically relevant actor for deposits):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

The swap extension has no equivalent mechanism to recover the true end-user identity.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict swaps to a specific set of addresses. To let those allowlisted users access the standard periphery, the admin must allowlist the router address. Once the router is allowlisted, **any unprivileged user** can bypass the restriction by calling `router.exactInputSingle` or `router.exactInput`. The allowlist provides zero protection on the router path.

Concrete consequences:
- Non-allowlisted users trade on a pool that was supposed to be restricted (e.g., KYC-gated, institutional-only, or beta-access pools).
- The pool admin has no way to simultaneously allow router access for legitimate users and block illegitimate users, because the extension cannot distinguish them.
- This is a direct curation failure and constitutes broken core pool functionality for any pool relying on `SwapAllowlistExtension` with the router.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary user-facing swap entry point in `metric-periphery`.
- Pool admins who want their allowlisted users to use the router (the normal UX path) will allowlist the router — this is the expected operational pattern.
- No warning in the documentation or NatDoc alerts admins to this identity-shift behavior.
- The existing test suite (`FullMetricExtension.t.sol`, `SwapAllowlistSubExtension.t.sol`) only tests direct pool calls, not router-mediated calls, so the bypass is untested and undetected.
- Any unprivileged user can trigger this with a single standard router call — no special setup required.

---

### Recommendation

The extension must gate on the **end user**, not the immediate caller of `pool.swap`. Two viable approaches:

1. **Pass the true initiator through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a protocol-level convention for the encoding.

2. **Check `sender` only when `sender` is not a known router; otherwise decode the real user from `extensionData`:** Requires the extension to be aware of trusted routers.

3. **Simplest safe fix:** Remove router allowlisting as a supported pattern and require all allowlisted users to call `pool.swap` directly. Document this constraint explicitly.

The root cause is that `sender` in `beforeSwap` is the pool's `msg.sender`, which is the router for all router-mediated swaps. Any fix must either change what is passed as `sender` or add a secondary identity channel.

---

### Proof of Concept

```solidity
// Scenario: pool admin wants only alice to swap; bob is not allowlisted.
// Admin allowlists alice AND the router (to let alice use the standard UI).

// Setup
swapExtension.setAllowedToSwap(address(pool), alice, true);
swapExtension.setAllowedToSwap(address(pool), address(router), true); // ← required for alice to use router

// Alice swaps through router — works as intended
vm.prank(alice);
router.exactInputSingle(ExactInputSingleParams({pool: pool, recipient: alice, ...}));

// Bob (not allowlisted) swaps through router — SHOULD revert, but PASSES
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({pool: pool, recipient: bob, ...}));
// ☢️ pool.swap() is called with msg.sender = router
// ☢️ extension checks allowedSwapper[pool][router] → true → no revert
// ☢️ bob successfully swaps on a pool he was supposed to be blocked from
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
