### Title
`SwapAllowlistExtension` gates on `sender` (direct pool caller = router) instead of the actual end user, allowing any user to bypass per-user swap restrictions via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`, where `sender` is the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` equals the router's address, not the actual end user. For router-based swaps to work at all, the pool admin must allowlist the router address — but doing so grants every user unrestricted access through the router, completely defeating the per-user curation the extension is designed to enforce.

---

### Finding Description

**Actor binding in `SwapAllowlistExtension`:**

`SwapAllowlistExtension.beforeSwap` receives `sender` as its first argument and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool (the extension is called by the pool). `sender` is whatever the pool passes as the first argument to `beforeSwap`.

**What the pool passes as `sender`:**

In `MetricOmmPool.swap()`, the pool calls:

```solidity
_beforeSwap(
    msg.sender,   // ← this is the direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` then forwards that value unchanged as the `sender` argument to the extension.

**What `msg.sender` is when the router is used:**

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
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

The router is `msg.sender` of `pool.swap()`, so `sender` in `beforeSwap` = **router address**, not the actual end user who called `exactInputSingle`.

**The bypass:**

A pool admin who wants per-user access control allowlists specific user addresses. But those users cannot swap through the router because the extension sees `sender = router`, which is not in `allowedSwapper`. To enable router-based swaps, the admin must allowlist the router address. Once the router is allowlisted, `allowedSwapper[pool][router] == true`, and **every user** who calls through the router passes the check — the per-user allowlist is completely bypassed.

The router does not inject any end-user identity into `extensionData`; the extension has no way to recover the actual caller.

**Contrast with `DepositAllowlistExtension`:**

`DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the position owner explicitly passed to `addLiquidity`), not `sender`. The swap extension has no equivalent explicit "swapper" parameter — only `sender` (direct caller) and `recipient` (output destination), neither of which is the end user when the router is involved.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to specific counterparties (e.g., KYC-verified addresses, institutional partners) loses that restriction entirely for any user who routes through `MetricOmmSimpleRouter`. The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use the supported public router path (broken core swap functionality).
- **Allowlist the router** → every user bypasses the per-user allowlist (curation failure, unauthorized trading in the pool).

Either outcome violates the invariant that "a curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the primary user-facing swap entrypoint in `metric-periphery`. Any pool admin who wants their allowlisted users to be able to trade through the standard router will naturally allowlist the router address. The mistake is not obvious: the admin sees "allowlist the router so my users can swap" without realizing this opens the gate to all users. The two contracts (`SwapAllowlistExtension` and `MetricOmmSimpleRouter`) are both production contracts in the same sub-repository and are expected to be used together.

---

### Recommendation

The `SwapAllowlistExtension` must gate on the actual end user, not the direct pool caller. Two approaches:

1. **Pass end-user identity through `extensionData`**: Have `MetricOmmSimpleRouter` inject `msg.sender` (the actual user) into `extensionData` before calling `pool.swap()`. The extension decodes and verifies this value. This requires a trusted encoding convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: If the pool's `recipient` is always the actual user (true for `exactInputSingle`), the extension could check `recipient`. However, this breaks for multi-hop `exactOutput` where intermediate recipients are the router itself.

The cleanest fix is approach 1, with the router encoding `msg.sender` as a standardized prefix in `extensionData` that the extension can verify came from a trusted router.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `beforeSwap`.
2. Pool admin calls `swapExtension.setAllowedToSwap(pool, alice, true)` — only Alice is allowed.
3. Pool admin also calls `swapExtension.setAllowedToSwap(pool, address(router), true)` — necessary for Alice to use the router.
4. Bob (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(...)`.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap succeeds despite not being in the allowlist.

Relevant code locations: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
