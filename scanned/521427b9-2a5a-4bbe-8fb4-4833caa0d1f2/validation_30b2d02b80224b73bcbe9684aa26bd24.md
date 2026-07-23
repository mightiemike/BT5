Looking at the `SwapAllowlistExtension`, `MetricOmmPool.swap()`, and `MetricOmmSimpleRouter` together reveals a concrete allowlist bypass.

---

### Title
SwapAllowlistExtension Gates on Router Address Instead of End-User, Allowing Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When any user routes through `MetricOmmSimpleRouter`, `sender` becomes the router's address. If the pool admin allowlists the router (the natural step to let approved users use the standard periphery), every unprivileged user can bypass the restriction by routing through the same contract.

### Finding Description

**Root cause — wrong identity in the hook argument**

`MetricOmmPool.swap()` passes its own `msg.sender` as the first argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol line 230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
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

`ExtensionCalling._beforeSwap` forwards that value verbatim as `sender` to every registered extension.

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the immediate caller of `pool.swap()`.

**Trigger path through the router**

`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly:

```solidity
// MetricOmmSimpleRouter.sol line 72-80
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

`msg.sender` of that `pool.swap()` call is the router contract. The pool therefore passes `sender = router` to the extension. The extension evaluates `allowedSwapper[pool][router]`, not the actual end-user's address.

**The dilemma that creates the bypass**

A pool admin who wants allowlisted users to be able to use the standard periphery router must call:

```solidity
setAllowedToSwap(pool, routerAddress, true);
```

Once that entry exists, `allowedSwapper[pool][router] == true` for every call that arrives through the router — regardless of who the real caller is. Any non-allowlisted user can call `router.exactInputSingle()` and the extension will approve the swap.

The same path exists for `exactInput`, `exactOutputSingle`, and `exactOutput` in the router, and for `simulateSwapAndRevert` on the pool itself.

### Impact Explanation
This is an admin-boundary break: an unprivileged user bypasses the pool admin's swap allowlist through a valid, public periphery path. The pool admin's intent — restricting swaps to a specific set of addresses — is silently voided for every user who routes through `MetricOmmSimpleRouter`. Any trade that the allowlist was meant to block (e.g., non-KYC'd counterparties, unauthorized market participants) executes at full oracle-anchored settlement, with real token transfers out of the pool to the unauthorized recipient.

### Likelihood Explanation
Medium-high. `MetricOmmSimpleRouter` is the primary user-facing swap interface. A pool admin who deploys a restricted pool and wants approved users to have a normal UX will naturally allowlist the router. The mistake is non-obvious because the admin sees individual user addresses in `setAllowedToSwap` and does not realize the router collapses all callers into one identity at the extension layer.

### Recommendation
The extension must verify the actual end-user identity, not the immediate `pool.swap()` caller. Two viable approaches:

1. **Pass real caller in `extensionData`**: The router encodes `msg.sender` into `extensionData`; the extension decodes and checks it. This requires a coordinated change in both the router and the extension.
2. **Dedicated `realSender` field in the hook interface**: Add a `realSender` argument to `IMetricOmmExtensions.beforeSwap` that the pool populates from a trusted transient-storage slot written by the router before calling `pool.swap()`.

Either way, the extension must not rely solely on the `sender` argument when the pool is callable through intermediary contracts.

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Pool admin calls setAllowedToSwap(pool, alice, true)
   → allowedSwapper[pool][alice] = true
3. Pool admin calls setAllowedToSwap(pool, router, true)
   → allowedSwapper[pool][router] = true   ← needed so Alice can use the router
4. Non-allowlisted Bob calls:
     router.exactInputSingle({pool: pool, tokenIn: T0, tokenOut: T1, ...})
5. Router calls pool.swap(recipient=bob, ...) with msg.sender = router.
6. Pool calls _beforeSwap(sender=router, ...).
7. Extension evaluates allowedSwapper[pool][router] == true → passes.
8. Swap executes; Bob receives T1 tokens from the restricted pool.
   allowedSwapper[pool][bob] was never set.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-core/contracts/MetricOmmPool.sol (L230-241)
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

**File:** metric-core/contracts/libraries/CallExtension.sol (L8-32)
```text
  function callExtension(address extension, bytes memory data) internal {
    (bool success, bytes memory result) = extension.call(data);
    if (!success) {
      if (result.length > 0) {
        assembly ("memory-safe") {
          revert(add(result, 32), mload(result))
        }
      }
      revert ExtensionCallFailed();
    }
    if (result.length < 32) {
      revert InvalidExtensionResponse();
    }
    bytes4 returnedSelector;
    assembly ("memory-safe") {
      returnedSelector := mload(add(result, 32))
    }
    bytes4 expectedSelector;
    assembly ("memory-safe") {
      expectedSelector := mload(add(data, 32))
    }
    if (returnedSelector != expectedSelector) {
      revert InvalidExtensionResponse();
    }
  }
```
