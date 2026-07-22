### Title
`SwapAllowlistExtension` gates the router address instead of the actual user, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` parameter, which the pool sets to `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the actual user. If the pool admin allowlists the router address (the natural step to enable router-based swaps), every unpermissioned user can bypass the per-user allowlist by calling any `exact*` function on the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check against the `sender` argument:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol L37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (correct — enforced by `onlyPool`). `sender` is the first argument forwarded by the pool, which is set in `ExtensionCalling._beforeSwap` to the `sender` parameter received from `MetricOmmPool.swap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← caller of pool.swap()
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router calls `pool.swap(params.recipient, ...)` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol L72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        ...
        params.extensionData
    );
```

The pool's `msg.sender` is the **router**, so `sender = router address` reaches the extension. The extension then checks `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`.

The same bypass applies to `exactInput`, `exactOutputSingle`, and `exactOutput`.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` to restrict trading to KYC'd or whitelisted addresses must also allowlist the router if they want those users to be able to trade via the supported periphery. Once the router address is in `allowedSwapper[pool]`, **any** unpermissioned user can call `router.exactInputSingle(...)` and the extension passes because it sees `sender = router` (allowlisted), not the actual caller. The curated pool's access control is completely nullified for all router-mediated swaps, allowing unauthorized users to trade against LP funds on a pool that was explicitly configured to restrict access.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entry point for EOAs. Any pool admin who wants allowlisted users to be able to use the router (the normal UX path) must allowlist the router address. This is the expected operational configuration, making the bypass reachable by any unpermissioned user in any production deployment of a curated pool.

---

### Recommendation

The extension must gate on the **economically relevant actor** — the address that initiated the transaction and will pay for the swap — not the intermediate contract. Two options:

1. **Check `recipient` instead of `sender`** — but `recipient` is the output token destination, not the payer, so this is also wrong.
2. **Require the router to forward the original `msg.sender`** as an explicit parameter in `extensionData`, and have the extension decode and check that address. This requires a coordinated change to the router and extension.
3. **Alternatively**, document that the router must never be allowlisted and that allowlisted users must call `pool.swap()` directly — but this breaks the intended UX and is operationally fragile.

The cleanest fix is to add an `originator` field to the swap extension data that the router populates with `msg.sender`, and have `SwapAllowlistExtension` decode and check that field when present.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension
  - Pool admin allowlists alice (KYC'd user): allowedSwapper[pool][alice] = true
  - Pool admin allowlists router (to let alice use the router): allowedSwapper[pool][router] = true
  - bob is NOT allowlisted

Attack:
  1. bob calls router.exactInputSingle({pool: pool, recipient: bob, ...})
  2. router calls pool.swap(bob, ...) — msg.sender to pool = router
  3. pool calls _beforeSwap(router, bob, ...)
  4. extension checks allowedSwapper[pool][router] → true → passes
  5. bob's swap executes on the curated pool despite not being allowlisted

Result: bob trades on a pool restricted to KYC'd users, bypassing the allowlist entirely.
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
