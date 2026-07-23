The call chain is fully traceable. Let me confirm the exact sender binding at each hop.

The full call chain is now confirmed. Here is the complete trace and verdict.

---

**Call chain (exact code path):**

```
attacker → router.multicall([exactInputSingle(restrictedPool)])
  └─ Address.functionDelegateCall(router, exactInputSingle)   // delegatecall: msg.sender = attacker inside router
       └─ exactInputSingle: IMetricOmmPoolActions(pool).swap(...)  // regular CALL: msg.sender to pool = router
            └─ MetricOmmPool.swap: _beforeSwap(msg.sender, ...)    // msg.sender = router address
                 └─ ExtensionCalling._beforeSwap(sender=router, ...)
                      └─ SwapAllowlistExtension.beforeSwap(sender=router)
                           └─ allowedSwapper[msg.sender][sender]
                              = allowedSwapper[pool][router]        // checks ROUTER, not attacker
```

**The critical binding:**

- `MetricOmmPool.swap` passes `msg.sender` (the router) as `sender` to `_beforeSwap`. [1](#0-0) 

- `ExtensionCalling._beforeSwap` forwards that `sender` argument verbatim to every extension. [2](#0-1) 

- `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` — `msg.sender` is the pool, `sender` is the router address, **not the actual end user**. [3](#0-2) 

- `exactInputSingle` calls `pool.swap()` as a regular external call (not delegatecall), so the pool always sees `msg.sender = router`, regardless of who called the router or whether multicall was used. [4](#0-3) 

- `multicall` uses `Address.functionDelegateCall`, which preserves `msg.sender = attacker` **inside** the router's own execution context, but this is irrelevant — the pool's `swap()` is reached via a regular `CALL`, so the pool always sees the router as the caller regardless. [5](#0-4) 

---

**Verdict: the multicall framing is a red herring, but the underlying bypass is real.**

The multicall ordering does not change anything — the bypass works identically with a single direct `router.exactInputSingle(restrictedPool)` call. The root cause is that `SwapAllowlistExtension` checks the immediate caller of `pool.swap()` (always the router), not the actual end user. This creates an inescapable dilemma for any pool admin who deploys this extension:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Individually-allowlisted users cannot use the router at all |
| **Allowlist the router** | Every user on the network can bypass the per-user allowlist |

There is no configuration that simultaneously allows router-mediated swaps for allowlisted users while blocking non-allowlisted users. The extension cannot enforce per-user access control for any swap that goes through the router.

---

### Title
SwapAllowlistExtension checks router address instead of actual user, allowing any user to bypass per-user swap allowlist via router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool, which is `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `msg.sender` to the pool is always the router contract address. The extension therefore checks whether the **router** is allowlisted, not whether the **actual end user** is allowlisted. Any user can bypass a per-user allowlist by routing through the router if the router address is in the allowlist.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to every configured extension. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]`. When `exactInputSingle` (or any router swap function) calls `pool.swap()`, the pool's `msg.sender` is the router contract, so the extension evaluates `allowedSwapper[pool][router]`. The actual end user's address is never checked. A pool admin who allowlists the router to permit router-mediated swaps for legitimate users inadvertently opens the pool to all users. Multicall is irrelevant — the bypass is identical with a single direct router call.

### Impact Explanation
The SwapAllowlistExtension's per-user access control is completely ineffective for router-mediated swaps. Any non-allowlisted user can swap on a restricted pool by calling `router.exactInputSingle(restrictedPool)`, provided the router is allowlisted. This breaks the pool curation invariant the extension is designed to enforce, allowing unauthorized participants to trade on pools intended to be private or restricted.

### Likelihood Explanation
Any pool that (a) deploys `SwapAllowlistExtension` for per-user access control and (b) allowlists the router to support normal UX is fully exposed. This is the expected operational configuration — a pool admin who wants to restrict swaps to specific users while still supporting the standard router would naturally allowlist the router. The bypass requires no special privileges, no flash loans, and no unusual token behavior.

### Recommendation
The extension must check the actual end user, not the immediate caller of `pool.swap()`. Two approaches:

1. **Pass the real user through the router:** Have `exactInputSingle` (and all router swap functions) encode `msg.sender` into `extensionData` and have the extension decode and verify it. The extension would then check the decoded user address rather than the `sender` argument.
2. **Check both router and user:** Require the pool admin to allowlist individual users (not the router), and have the router forward the real user address in a verifiable way (e.g., signed or via a trusted forwarder pattern).

### Proof of Concept
```solidity
// Setup: pool with SwapAllowlistExtension, only router is allowlisted
extension.setAllowedToSwap(restrictedPool, address(router), true);
// attacker is NOT allowlisted:
assertFalse(extension.isAllowedToSwap(restrictedPool, attacker));

// Bypass: attacker routes through the router
vm.prank(attacker);
router.exactInputSingle(ExactInputSingleParams({
    pool: restrictedPool,
    ...
}));
// Swap succeeds — extension saw sender=router (allowlisted), not sender=attacker
```

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

**File:** metric-core/contracts/ExtensionCalling.sol (L149-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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
