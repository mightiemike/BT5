### Title
`SwapAllowlistExtension` checks the router's address instead of the end user's address, allowing any user to bypass a curated pool's swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument passed by the pool. When a user swaps directly, `sender` equals the user. When a user swaps through `MetricOmmSimpleRouter`, `sender` equals the router's address. If the pool admin allowlists the router (required for router-mediated swaps to succeed), every user — including those not individually allowlisted — can bypass the curation policy by routing through the public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // ← whoever called pool.swap()
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap()` then checks whether that `sender` is on the allowlist:

```solidity
// SwapAllowlistExtension.sol L37-38
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()` (or any `exact*` variant), the router calls `pool.swap()` directly, making `msg.sender` inside the pool equal to the router's address:

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

The extension therefore evaluates `allowedSwapper[pool][router]` instead of `allowedSwapper[pool][user]`. For router-mediated swaps to work at all on an allowlisted pool, the pool admin must add the router to the allowlist. Once the router is allowlisted, the guard is effectively open to every user who calls through it, regardless of their individual allowlist status.

The same substitution occurs in multi-hop `exactInput` (intermediate hops use `address(this)` as payer but still call `pool.swap()` from the router) and in the recursive `exactOutput` callback path.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a specific set of counterparties (e.g., KYC'd addresses, protocol-owned bots, or whitelisted market makers). Any non-allowlisted user can bypass this restriction by calling `MetricOmmSimpleRouter.exactInputSingle()` or `exactInput()` on the same pool, provided the router address is itself allowlisted. The curation policy is silently voided: disallowed users trade freely, the pool's LP assets are exposed to unintended counterparties, and any fee or risk model that depends on the allowlist is broken.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard public swap entrypoint documented and deployed alongside the protocol. Any user who reads the periphery contracts can discover the bypass without any privileged access. The only precondition is that the pool admin has allowlisted the router — a necessary step for the router to be usable at all on an allowlisted pool. The bypass is therefore reachable on every allowlisted pool that also supports router-mediated swaps.

---

### Recommendation

The extension must gate the **end user**, not the intermediary. Two complementary fixes:

1. **Pass the original user through the router.** Add a `swapper` parameter to the router's `exact*` functions and forward it as the `sender` argument to `pool.swap()`. The pool would need a corresponding change to accept an explicit sender, or the router could encode the real user in `extensionData` and the extension could decode it — but this requires trust in the router.

2. **Check `msg.sender` (the pool) and the decoded real user in the extension.** A cleaner approach: the router encodes the real user in `extensionData`, and the extension decodes and checks that address instead of the pool-supplied `sender`. This keeps the check inside the extension without modifying the core pool interface.

The simplest safe fix is to have the extension reject any `sender` that is a known router/intermediary and require the real user to be encoded in `extensionData`, or to require direct pool calls for allowlisted pools.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension.
  - Pool admin allowlists only `trustedUser` and the router address
    (router must be allowlisted for router-mediated swaps to work).
  - `attacker` is NOT on the allowlist.

Step 1 — Direct swap (blocked correctly):
  attacker calls pool.swap(...) directly
  → SwapAllowlistExtension checks allowedSwapper[pool][attacker] → false → revert NotAllowedToSwap ✓

Step 2 — Router swap (bypass):
  attacker calls MetricOmmSimpleRouter.exactInputSingle({pool: pool, ...})
  → router calls pool.swap(...)
  → pool passes msg.sender = router as `sender` to _beforeSwap
  → SwapAllowlistExtension checks allowedSwapper[pool][router] → true (router is allowlisted)
  → swap executes successfully ✗ — attacker bypassed the allowlist
```

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
