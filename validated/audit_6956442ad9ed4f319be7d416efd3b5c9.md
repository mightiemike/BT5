### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual user, allowing any user to bypass the swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the router contract, not the actual user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every unpermissioned user can bypass the curated-pool allowlist by routing through the router.

---

### Finding Description

The call chain for a router-mediated swap is:

```
User (Alice) → MetricOmmSimpleRouter.exactInputSingle()
    → pool.swap(recipient, ..., extensionData)   // msg.sender = router
        → _beforeSwap(msg.sender=router, ...)
            → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                → allowedSwapper[pool][router]   // checks router, not Alice
```

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap` directly: [1](#0-0) 

The pool passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [2](#0-1) 

`ExtensionCalling._beforeSwap` forwards that `sender` value unchanged to the extension: [3](#0-2) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender (pool)][sender]`, where `sender` is the router: [4](#0-3) 

The pool admin faces an impossible choice:

- **Do not allowlist the router** → allowlisted users cannot use `MetricOmmSimpleRouter` at all on this pool.
- **Allowlist the router** → every user, including those the allowlist was meant to block, can bypass the guard by routing through the router.

There is no configuration that simultaneously permits router-mediated swaps for allowlisted users and blocks router-mediated swaps for non-allowlisted users.

The `DepositAllowlistExtension` does not share this flaw because it checks `owner` (the position owner supplied by the caller), not `sender`: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC'd counterparties, protocol-owned addresses, or whitelisted market makers) can be bypassed by any unpermissioned user routing through `MetricOmmSimpleRouter`. The bypassing user executes swaps at oracle-derived prices against LP funds, extracting value that the pool admin intended to restrict. This is a direct loss of curation policy and LP principal protection on restricted pools.

---

### Likelihood Explanation

`MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps. Any pool admin who wants allowlisted users to be able to use the router must allowlist the router address. Once the router is allowlisted, the bypass is trivially available to every user with no special knowledge or capital beyond the swap amount. The trigger is a single public call to `exactInputSingle` or `exactInput`.

---

### Recommendation

The extension must resolve the actual end-user identity rather than the immediate `msg.sender` of the pool call. Two sound approaches:

1. **Pass the original initiator through the router.** Have `MetricOmmSimpleRouter` forward `msg.sender` (the real user) as an additional field in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that field when `sender` is a known router. This requires a protocol-level convention for the extensionData format.

2. **Check `sender` against a router registry and resolve the payer.** The router stores the real payer in transient storage (`_getPayer()`). Expose a standard interface so extensions can query the real initiator from a trusted router, and check that address against the allowlist instead of `sender`.

The simplest safe fix is to make the allowlist check the `sender` argument only when `sender` is not a registered router, and require the router to attest the real user identity in `extensionData` when it is.

---

### Proof of Concept

Setup:
- Pool P is deployed with `SwapAllowlistExtension` as `EXTENSION_1` on the `beforeSwap` order.
- Pool admin allowlists Alice (`allowedSwapper[P][Alice] = true`) and the router (`allowedSwapper[P][router] = true`) so Alice can use the router.
- Bob is not allowlisted.

Attack:
1. Bob calls `MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})`.
2. Router calls `P.swap(recipient=Bob, ...)` with `msg.sender = router`.
3. Pool calls `_beforeSwap(sender=router, ...)`.
4. `SwapAllowlistExtension.beforeSwap` evaluates `allowedSwapper[P][router] == true` → passes.
5. Bob's swap executes at oracle price against LP funds.

Expected: revert `NotAllowedToSwap`.
Actual: swap succeeds. [6](#0-5) [7](#0-6)

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
