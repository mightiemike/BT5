### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address as `sender` instead of the end user, allowing any unprivileged user to bypass the per-user swap allowlist on curated pools — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension` gates swaps by checking the `sender` argument passed by the pool, which equals `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of `pool.swap()`, so the extension checks `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][endUser]`. If the pool admin allowlists the router (a natural step to let allowlisted users trade via the official periphery), every unprivileged user can bypass the per-user allowlist by routing through the router.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension.beforeSwap`:**

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`msg.sender` here is the pool (enforced by `onlyPool` in the base). `sender` is the first argument forwarded by the pool from its own `msg.sender`.

**Pool passes its own `msg.sender` as `sender`:**

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- this becomes `sender` in the extension
    recipient,
    ...
);
```

**Router calls `pool.swap()` directly, making itself `msg.sender`:**

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:72-80
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

The router never forwards the original `msg.sender` (the end user) to the pool. The pool therefore passes `sender = router` to the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

**Consequence:** The pool admin cannot grant per-user router access. The only options are:
1. Allowlist the router → every user on the network can bypass the allowlist via the router.
2. Do not allowlist the router → allowlisted users cannot use the router at all.

If the admin chooses option 1 (the natural choice to let their curated users trade via the official periphery), the allowlist is completely open to any caller who routes through `MetricOmmSimpleRouter`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific addresses (e.g., KYC-verified counterparties, institutional participants, or protocol-controlled addresses) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The allowlist guard fails open for the entire router-mediated swap path, which is the primary supported periphery entrypoint. This constitutes an admin-boundary break: an unprivileged path (`MetricOmmSimpleRouter`) bypasses the pool admin's configured role check (`allowedSwapper`), matching the allowed impact gate.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the production periphery contract and the expected entrypoint for most users. A pool admin who wants their allowlisted users to be able to trade via the router will naturally add the router to the allowlist. The mistake is non-obvious: the admin believes they are enabling specific users, but they are in fact opening the gate to all users. The trigger is a single `setAllowedToSwap(pool, router, true)` call by the pool admin, which is a routine operational action.

---

### Recommendation

The extension must gate the **economic actor** (the end user), not the intermediate contract. Two approaches:

1. **Pass end-user identity through `extensionData`:** The router encodes `msg.sender` into `extensionData`; the extension decodes and verifies it. The pool admin allowlists end users, not the router.
2. **Separate router-level allowlist:** The router enforces its own per-user allowlist before calling the pool, and the extension trusts only the router's attestation (requires a trusted-router model).

The current design makes it structurally impossible to enforce per-user restrictions on router-mediated swaps.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` and sets `allowAllSwappers[pool] = false`.
2. Admin allowlists `user1`: `setAllowedToSwap(pool, user1, true)`.
3. Admin allowlists the router so `user1` can trade via periphery: `setAllowedToSwap(pool, router, true)`.
4. `user2` (not allowlisted) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router calls `pool.swap(recipient, ...)` — pool's `msg.sender` is the router.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
8. `user2`'s swap executes successfully on the curated pool, bypassing the allowlist entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/extensions/base/BaseMetricExtension.sol (L31-35)
```text
  modifier onlyPoolAdmin(address pool_) {
    address poolAdmin = IMetricOmmPoolFactory(FACTORY).poolAdmin(pool_);
    if (msg.sender != poolAdmin) revert OnlyPoolAdmin(pool_, msg.sender, poolAdmin);
    _;
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
