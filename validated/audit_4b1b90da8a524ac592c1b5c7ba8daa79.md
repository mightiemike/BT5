### Title
`SwapAllowlistExtension.beforeSwap` checks the router address instead of the actual swapper, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` - (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` mediates the swap, `msg.sender` to the pool is the **router contract**, not the actual user. If a pool admin allowlists the router address (the only way to permit router-mediated swaps for their allowlisted users), every non-allowlisted user can bypass the per-user gate by routing through the router.

---

### Finding Description

**Call path:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
     → IMetricOmmPoolActions(pool).swap(recipient, ...)   // msg.sender = router
     → MetricOmmPool._beforeSwap(msg.sender=router, ...)
     → ExtensionCalling._callExtensionsInOrder(...)
     → SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

**`MetricOmmPool.swap` passes `msg.sender` as `sender`:** [1](#0-0) 

**`ExtensionCalling._beforeSwap` forwards it unchanged:** [2](#0-1) 

**`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = router:** [3](#0-2) 

**`MetricOmmSimpleRouter.exactInputSingle` calls the pool directly without forwarding the actual user's identity:** [4](#0-3) 

The router never passes the originating user's address to the pool. The pool has no mechanism to recover it. The extension therefore evaluates `allowedSwapper[pool][routerAddress]` for every router-mediated swap, regardless of who called the router.

A pool admin who wants allowlisted users to be able to use the router faces an impossible choice:

| Admin action | Allowlisted users via router | Non-allowlisted users via router |
|---|---|---|
| Do **not** allowlist router | ✗ blocked | ✗ blocked |
| **Allowlist router** | ✓ allowed | **✓ allowed (bypass)** |

There is no configuration that permits allowlisted users through the router while blocking non-allowlisted users.

---

### Impact Explanation

Any non-allowlisted user can trade on a curated pool that has `SwapAllowlistExtension` configured, provided the pool admin has allowlisted the router address. The allowlist — the sole mechanism for restricting swap access on curated pools — is rendered ineffective for all router-mediated swaps. This breaks the core invariant stated in the codebase's own audit targets:

> *"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it."* [5](#0-4) 

Unauthorized swappers on a restricted pool can extract value from LPs through arbitrage or directional trading that the pool admin explicitly intended to prevent.

---

### Likelihood Explanation

The trigger is a routine admin action: allowlisting the router so that allowlisted users can access the pool through the standard periphery. This is the expected operational pattern for any pool that uses both an allowlist and the router. The bypass is then reachable by any unprivileged user with no special setup.

---

### Recommendation

The `SwapAllowlistExtension` must check the economically relevant actor, not the intermediary. Two viable approaches:

1. **Extension-data forwarding**: The router encodes the originating user's address in `extensionData`; the extension decodes and checks it. This requires the router to be trusted to forward the correct address.
2. **Recipient-based check**: Gate on `recipient` (the address that receives output tokens) rather than `sender`, since the recipient is the economically relevant party for output-side curated pools.

The `DepositAllowlistExtension` already demonstrates the correct pattern by checking `owner` (the position beneficiary) rather than `sender` (the payer): [6](#0-5) 

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);

// Admin allowlists the router so allowedUser can use it.
swapExtension.setAllowedToSwap(address(pool), address(router), true);

// Non-allowlisted attacker bypasses the gate via the router.
vm.prank(attacker); // attacker is NOT in allowedSwapper[pool]
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        recipient: attacker,
        tokenIn: address(token0),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        priceLimitX64: 0,
        deadline: block.timestamp + 1,
        extensionData: ""
    })
);
// Swap succeeds: extension saw sender=router (allowlisted), not attacker (blocked).
```

The extension receives `sender = address(router)`, looks up `allowedSwapper[pool][router] = true`, and passes — the attacker's address is never checked.

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

**File:** generate_scanned_questions.py (L733-738)
```python
            title="allowlist bypass",
            question_focus="a curated pool's allowlist can be bypassed through a public router or liquidity-adder path",
            exploit="Enter through the supported periphery path rather than the direct pool call and see whether the identity check changes.",
            invariant="A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it.",
            impact="High direct loss or curation failure if disallowed users can still trade or deposit.",
        ),
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
