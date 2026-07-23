### Title
SwapAllowlistExtension Checks Router Address Instead of End-User, Allowing Any User to Bypass Per-User Swap Restrictions via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap` is the **router contract address**, not the end user. If the pool admin allowlists the router to enable router-mediated swaps, every user—including non-allowlisted ones—can bypass the per-user restriction by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Here `msg.sender` is the pool (correct) and `sender` is the value passed by the pool from `ExtensionCalling._beforeSwap`:

```solidity
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

The pool populates `sender` as `msg.sender` of the `pool.swap` call:

```solidity
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    ...
    _beforeSwap(msg.sender, recipient, ...);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap` directly:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ..., params.extensionData);
```

The `msg.sender` of `pool.swap` is the **router contract**, not the end user. Therefore `SwapAllowlistExtension` checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][end_user]`.

This creates an irreconcilable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do NOT allowlist the router | Allowlisted users cannot use the router at all |
| Allowlist the router | ALL users (including non-allowlisted) can swap through the router |

There is no configuration that simultaneously allows allowlisted users to use the router while blocking non-allowlisted users.

The same issue applies to `exactInput` (multi-hop) and `exactOutput` (recursive callback) paths in `MetricOmmSimpleRouter`, since all of them call `pool.swap` with `msg.sender = router`.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to specific addresses (e.g., KYC'd counterparties, whitelisted market makers) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker can execute swaps against a pool that was supposed to be curated, potentially:

- Trading against LP positions at oracle-derived prices without authorization
- Draining LP value through repeated swaps on a pool whose admin believed access was restricted
- Violating regulatory or contractual access controls on curated pools

This is a direct loss of LP principal and a broken core pool functionality (the allowlist guard fails open for the supported periphery path).

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported periphery swap entrypoint. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants to support router-mediated swaps must allowlist the router, at which point the guard is fully bypassed. The router address is a single, publicly known contract, so the bypass requires no special setup beyond a standard router call.

---

### Recommendation

The `SwapAllowlistExtension` should check the **end user** rather than the immediate `msg.sender` of `pool.swap`. Two approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` (the end user) into `extensionData` and the extension decodes and checks it. This requires a protocol-level convention.

2. **Check `sender` against a router registry and then verify the actual payer**: The extension recognizes known router addresses and, when `sender` is a router, requires the extension payload to carry the authenticated end-user identity.

3. **Enforce allowlist at the router level**: The router checks the allowlist before calling `pool.swap`, but this is weaker because it can be bypassed by calling `pool.swap` directly with the router's address spoofed (not possible) or by deploying a custom router.

The cleanest fix is option 1: the pool's `beforeSwap` hook receives both `sender` (the immediate caller) and an authenticated `originator` field that the router populates from its own `msg.sender`.

---

### Proof of Concept

```
Setup:
  - Pool deployed with SwapAllowlistExtension as beforeSwap hook
  - Pool admin calls setAllowedToSwap(pool, router, true)   // to enable router swaps
  - Pool admin calls setAllowedToSwap(pool, alice, true)    // alice is the only intended user
  - Bob is NOT allowlisted

Attack:
  1. Bob calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  2. Router calls pool.swap(...) with msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  5. Bob's swap executes successfully against the restricted pool

Result:
  - Bob (non-allowlisted) swaps against a pool that was supposed to be restricted to alice only
  - The per-user allowlist guard is completely bypassed via the supported router path
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
