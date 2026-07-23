### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any caller to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks `sender`, which is `msg.sender` of the pool's `swap()` call. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is the router contract, not the end user. If the pool admin allowlists the router to enable router-based swaps, every user — including those not individually allowlisted — can bypass the per-user gate by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...) [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → checks allowedSwapper[pool][router]
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` as `sender` to `_beforeSwap`:

```solidity
_beforeSwap(
  msg.sender,   // ← router address when called via router
  recipient,
  ...
);
```

`ExtensionCalling._beforeSwap` encodes this and calls the extension:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool (correct), and `sender` = router (wrong actor). The extension checks whether the **router** is allowlisted, not whether the **end user** is allowlisted.

The test suite (`FullMetricExtensionTest`) only exercises direct pool calls via `TestCaller` contracts, so `sender` = `TestCaller` = the allowlisted entity. The router path is never tested, masking the mismatch.

---

### Impact Explanation

Two fund-impacting outcomes arise:

**Outcome A — Complete allowlist bypass (High):**
If the pool admin allowlists the router (`setAllowedToSwap(pool, router, true)`) to enable router-based swaps, every user — including those explicitly not allowlisted — can call `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutput` and swap freely in the curated pool. The per-user allowlist is entirely inoperative for the router path. Unauthorized users gain access to a pool whose LP positions were sized and priced under the assumption of a restricted counterparty set, directly exposing LP principal to unintended adverse selection.

**Outcome B — Allowlisted users locked out of the router (Medium):**
If the pool admin does not allowlist the router, individually allowlisted users cannot use the router at all. The router is the primary public swap entrypoint; blocking it breaks the core swap flow for legitimate users.

---

### Likelihood Explanation

The router is the canonical user-facing swap path. Any pool admin who deploys a `SwapAllowlistExtension`-gated pool and also wants users to swap through the router must allowlist the router — triggering Outcome A. This is a natural, expected operational step, not an exotic configuration. The likelihood of Outcome A is therefore high whenever the pool is intended to be accessible via the router.

---

### Recommendation

The `SwapAllowlistExtension` must gate the **end user**, not the intermediary router. Two viable approaches:

1. **Decode end-user identity from `extensionData`:** Have the router encode `msg.sender` (the end user) into `extensionData` before forwarding to the pool. The extension decodes and checks that address. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`:** For single-hop swaps, `recipient` is the end user. However, for multi-hop swaps the recipient of intermediate hops is the router itself, so this is not universally correct.

3. **Structural fix:** Add a dedicated `swapper` field to the extension interface (separate from `sender`) that the pool populates from a verified source (e.g., a transient-storage context set by the router before calling the pool), so the extension always sees the true economic actor regardless of intermediary.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);
// Pool admin also allowlists the router so router-based swaps work for allowedUser
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Attack: bannedUser routes through the router
vm.startPrank(bannedUser);
token0.approve(address(router), type(uint256).max);
// This succeeds — extension checks allowedSwapper[pool][router] = true
router.exactInputSingle(IMetricOmmSimpleRouter.ExactInputSingleParams({
    pool:            address(pool),
    tokenIn:         address(token0),
    recipient:       bannedUser,
    amountIn:        1000,
    amountOutMinimum: 0,
    zeroForOne:      true,
    priceLimitX64:   0,
    deadline:        block.timestamp + 1,
    extensionData:   ""
}));
// bannedUser successfully swaps in a pool they were explicitly excluded from
```

The extension's check at line 37 of `SwapAllowlistExtension.sol` resolves to `allowedSwapper[pool][router] == true`, passing unconditionally for every user who routes through the router. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
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
```

**File:** metric-periphery/test/extensions/FullMetricExtension.t.sol (L68-74)
```text
  function test_allowedSwapSucceeds() public {
    depositExtension.setAllowedToDeposit(address(pool), _getCallerAddress(0), true);
    swapExtension.setAllowedToSwap(address(pool), address(callers[0]), true);

    _addLiquidity(0, -5, 4, 100_000, EXTENSION_TEST_SALT);
    _swap(0, users[0], false, int128(1000), type(uint128).max);
  }
```
