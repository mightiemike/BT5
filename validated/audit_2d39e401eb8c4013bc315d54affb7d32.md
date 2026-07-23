### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the Actual User — Allowlist Fully Bypassed via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is always `msg.sender` of the `pool.swap()` call. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The allowlist therefore checks whether the **router** is permitted, not whether the **user** is permitted. Any user can bypass a per-user swap allowlist by calling the router instead of the pool directly.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle / exactInput / exactOutputSingle / exactOutput
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   [msg.sender = router]
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
     → allowedSwapper[pool][router]  ← checks router, not user
```

**Pool `swap` passes `msg.sender` as `sender`:** [1](#0-0) 

**`SwapAllowlistExtension.beforeSwap` checks `sender` (the router) against the allowlist:** [2](#0-1) 

**Router calls `pool.swap` directly, making itself `msg.sender` in the pool:** [3](#0-2) 

The same pattern repeats for every router entry point (`exactInput`, `exactOutputSingle`, `exactOutput`). [4](#0-3) 

**Contrast with `DepositAllowlistExtension`**, which correctly gates the `owner` argument (the position owner), not `sender` (the payer/operator): [5](#0-4) 

The deposit extension is not vulnerable because it gates `owner`, which is an explicit parameter the pool does not overwrite with `msg.sender`. The swap extension is vulnerable because it gates `sender`, which the pool always sets to `msg.sender`.

---

### Impact Explanation

Two mutually exclusive failure modes, both fund-impacting:

1. **Allowlist bypass (primary impact):** The pool admin allowlists the router so that router-mediated swaps work. Once the router is allowlisted, **every user on the network** can call `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` and swap on the pool, regardless of whether they are individually allowlisted. The per-user gate is completely defeated. Pools intended for KYC'd, institutional, or otherwise restricted participants are open to all.

2. **Legitimate users blocked (secondary impact):** If the pool admin does not allowlist the router, individually allowlisted users who need multi-hop routing (`exactInput` / `exactOutput`) are blocked even though they are permitted. Core swap functionality is broken for those users.

Both outcomes break the core invariant that the allowlist gates the economically relevant swapper.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary production swap entry point for end users; virtually all non-technical users will route through it.
- The pool admin has no way to configure the extension to check the real user identity — the `sender` argument is hardcoded to `msg.sender` in the pool and cannot be overridden by the caller.
- No special privileges, flash loans, or unusual conditions are required. Any user with a wallet can call the router.
- The `multicall` path on the router uses `delegatecall` (preserving `msg.sender` within the router context) but still calls `pool.swap` from the router, so the pool still sees the router as `msg.sender`. [6](#0-5) 

---

### Recommendation

The extension must check the **original user** identity, not the immediate pool caller. Two sound approaches:

1. **Pass the real user through `extensionData`:** The router encodes `msg.sender` into `extensionData` before forwarding to the pool. The `SwapAllowlistExtension` decodes and verifies this value. This requires the extension to trust that the pool (not an arbitrary caller) forwarded the data, which is already guaranteed by `onlyPool`.

2. **Check `recipient` instead of `sender`:** For single-hop swaps the recipient is typically the user. This is imprecise for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router allowlist entry:** Allowlist the router as a trusted forwarder and require the router to attest the real user in `extensionData`, with the extension verifying the attestation signature or address.

The cleanest fix is option 1: the router always appends `abi.encode(msg.sender)` to `extensionData`, and the extension decodes and gates on that value when `sender` is a known router.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `alice` is allowlisted
swapExtension.setAllowedToSwap(address(pool), alice, true);
// router is NOT allowlisted, bob is NOT allowlisted

// Direct swap by bob → correctly reverts
vm.prank(bob);
pool.swap(bob, false, 1000, type(uint128).max, "", "");
// → NotAllowedToSwap ✓

// Router swap by bob → PASSES (router is msg.sender in pool, not bob)
// Step 1: allowlist the router so alice can use it
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Step 2: bob calls router — pool sees router as sender, allowlist passes
vm.prank(bob);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    tokenIn: address(token1),
    recipient: bob,
    amountIn: 1000,
    amountOutMinimum: 0,
    zeroForOne: false,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    extensionData: ""
}));
// → succeeds; bob swapped despite not being allowlisted ✗
```

The `SwapAllowlistExtension` checks `allowedSwapper[pool][router]` (true) and passes, never inspecting `bob`'s address. [7](#0-6) [8](#0-7)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
  }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-125)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
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
