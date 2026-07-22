### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual Swapper, Allowing Any User to Bypass the Allowlist via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, `msg.sender` at the pool is the **router contract**, not the actual user. If the router is allowlisted (required for any router-mediated swap to work), every unpermissioned user can bypass the allowlist by calling the router instead of the pool directly.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it verbatim to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` into the call to each extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly, making the router the `msg.sender` at the pool level: [4](#0-3) 

The router stores the **actual user** only in its own transient callback context for payment settlement — it is never forwarded to the pool or to extensions as the identity being gated. [5](#0-4) 

This creates an irresolvable dilemma for pool admins:

- **If the router is NOT allowlisted**: every allowlisted user who tries to swap via the router is blocked, even though they are individually permitted. The router is a core periphery contract that users are expected to use.
- **If the router IS allowlisted** (the only way to let legitimate users swap via the router): the allowlist check reduces to `allowedSwapper[pool][router] == true`, which passes for **every caller** of the router, including completely unpermissioned users.

---

### Impact Explanation

Any user who is not on the `SwapAllowlistExtension` allowlist can bypass the restriction entirely by calling `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) targeting the restricted pool. The extension sees `sender = router`, which is allowlisted, and the swap proceeds. The actual user's address is never checked.

This breaks the core access-control invariant of the `SwapAllowlistExtension`: that only explicitly permitted addresses may swap in a restricted pool. Pools that rely on this extension for KYC gating, market-maker-only access, or regulatory compliance are fully unprotected against router-mediated bypass.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the standard, documented periphery entry point for swaps.
- Any attacker who reads the contract can discover the bypass without any privileged access.
- No special token behavior, flash loans, or admin cooperation is required.
- The bypass is reachable on every pool that has `SwapAllowlistExtension` configured in `BEFORE_SWAP_ORDER` and has the router allowlisted.

---

### Recommendation

`SwapAllowlistExtension.beforeSwap` must gate on the **economically relevant actor**, not the intermediary. Two complementary fixes:

1. **Pass the original user through `extensionData`**: The router encodes the real user in `extensionData` and the extension decodes and checks it. This requires a protocol-level convention.
2. **Check `sender` against the allowlist and require that the router is never allowlisted**: Document that the router must not be allowlisted and that users must call the pool directly for allowlisted pools. This is a UX regression but closes the bypass.
3. **Preferred fix**: Redesign the extension to accept an explicit `realSwapper` field in `extensionData` that the router always populates with `msg.sender` before calling the pool, and verify it in `beforeSwap`.

---

### Proof of Concept

```
Setup:
  1. Deploy pool with SwapAllowlistExtension as BEFORE_SWAP_ORDER extension.
  2. Pool admin calls swapExtension.setAllowedToSwap(pool, alice, true).
  3. Pool admin calls swapExtension.setAllowedToSwap(pool, router, true)
     (required so alice can use the router).

Attack:
  4. Charlie (not allowlisted) calls:
       router.exactInputSingle(ExactInputSingleParams({
           pool: restrictedPool,
           recipient: charlie,
           zeroForOne: true,
           amountIn: X,
           ...
       }))
  5. Router calls pool.swap(recipient=charlie, ...) with msg.sender = router.
  6. Pool calls _beforeSwap(sender=router, ...).
  7. SwapAllowlistExtension checks allowedSwapper[pool][router] == true → passes.
  8. Charlie's swap executes in the restricted pool.

Expected: revert NotAllowedToSwap.
Actual:   swap succeeds; Charlie bypasses the allowlist.
``` [3](#0-2) [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-177)
```text
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
