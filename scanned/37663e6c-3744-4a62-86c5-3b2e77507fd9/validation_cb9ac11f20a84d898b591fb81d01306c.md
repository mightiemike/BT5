### Title
SwapAllowlistExtension Checks Router Address Instead of End-User Identity, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the allowlist checks the router's address, not the end user's. If the pool admin allowlists the router (the natural action to enable router-based swaps for their permitted users), every unpermitted user can bypass the allowlist by calling through the router.

---

### Finding Description

**Allowlist check in `SwapAllowlistExtension`:**

```solidity
// SwapAllowlistExtension.sol L37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is the value the pool passes as the first argument to `beforeSwap`.

**How the pool populates `sender`:**

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

**How the router calls the pool:**

```solidity
// MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The router calls `pool.swap()` directly. Inside the pool, `msg.sender` is the router's address. The pool therefore passes `router` as `sender` to `_beforeSwap`, and the extension evaluates:

```
allowedSwapper[pool][router]
```

not

```
allowedSwapper[pool][end_user]
```

**Attack path:**

1. Pool admin deploys a restricted pool with `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER`.
2. Pool admin allowlists specific users: `setAllowedToSwap(pool, user1, true)`.
3. Pool admin also allowlists the router so that permitted users can use the standard periphery: `setAllowedToSwap(pool, router, true)`.
4. Any unpermitted user calls `router.exactInputSingle(pool, ...)`. The router calls `pool.swap()`. The extension sees `sender = router`, finds `allowedSwapper[pool][router] = true`, and passes. The swap executes.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap()` with the router as `msg.sender`.

The `DepositAllowlistExtension` does **not** share this flaw: it checks `owner` (the LP position owner explicitly passed by the caller), not `sender`, so the identity of the depositing party is preserved through the liquidity adder.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a defined set of addresses (e.g., KYC-gated counterparties, institutional LPs, or protocol-controlled accounts). Once the router is allowlisted — a necessary step for permitted users to use the standard periphery — the allowlist is completely ineffective: any address can swap by routing through `MetricOmmSimpleRouter`. Unauthorized traders can drain LP value at oracle-derived prices, bypassing the access control the pool admin believed was in place.

---

### Likelihood Explanation

The pool admin must allowlist the router for the bypass to be reachable. This is a realistic and expected operational step: permitted users naturally want to use the standard router rather than calling the pool directly. The admin has no in-protocol signal that allowlisting the router opens the gate to all users. The bypass requires no special privileges, no flash loans, and no contract deployment — a single `exactInputSingle` call suffices.

---

### Recommendation

The extension must gate on the **end user's identity**, not the direct caller of `pool.swap()`. Two concrete options:

1. **Pass the original user through `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling the pool. The `SwapAllowlistExtension` decodes and checks that address. This requires a convention between the router and the extension but no core changes.

2. **Check both `sender` and a decoded user address**: The extension accepts an optional ABI-encoded address in `extensionData`; if present, it checks that address; otherwise it falls back to `sender`. Permitted users calling the pool directly pass no extension data; router calls encode the user.

Until a fix is deployed, the pool admin must **not** allowlist the router address. Permitted users must call `pool.swap()` directly.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `permittedUser` is allowlisted.
// Pool admin also allowlists the router to let permittedUser use it.
swapAllowlist.setAllowedToSwap(address(pool), address(router), true);
swapAllowlist.setAllowedToSwap(address(pool), permittedUser, true);

// Attacker (not allowlisted) calls through the router.
// The extension sees sender = router, which IS allowlisted → swap succeeds.
vm.prank(attacker);
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        zeroForOne:      true,
        recipient:       attacker,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        priceLimitX64:   0,
        tokenIn:         address(token0),
        deadline:        block.timestamp + 1,
        extensionData:   ""
    })
);
// attacker receives token1 despite never being allowlisted.
assertGt(token1.balanceOf(attacker), 0);
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
