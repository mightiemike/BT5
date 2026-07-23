### Title
SwapAllowlistExtension Checks Router Address as Swapper Identity, Enabling Complete Allowlist Bypass via MetricOmmSimpleRouter — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` argument against a per-pool allowlist. The pool passes `msg.sender` of `pool.swap()` as `sender`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. The extension therefore checks the router's address, not the user's. If the pool admin allowlists the router (a natural action to enable router-mediated swaps for allowlisted users), every user — including those not on the allowlist — can bypass the guard by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → IMetricOmmPoolActions(pool).swap(...)   // msg.sender = router
              → MetricOmmPool.swap()
                   → _beforeSwap(msg.sender, ...)  // sender = router
                        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                             → allowedSwapper[pool][router]  // checks router, not user
```

**Step 1 — Pool passes `msg.sender` as `sender`:**

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)`. When the router is the caller, `msg.sender` = router. [1](#0-0) 

**Step 2 — ExtensionCalling forwards `sender` verbatim:**

`_beforeSwap` in `ExtensionCalling.sol` encodes `sender` into the call to the extension without modification. [2](#0-1) 

**Step 3 — SwapAllowlistExtension checks the wrong actor:**

The extension checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool (correct) and `sender` = router (wrong — should be the end user). [3](#0-2) 

**Step 4 — Router calls pool directly, no user identity forwarded:**

`exactInputSingle` (and all other router entry points) call `pool.swap()` directly. There is no mechanism to forward the original `msg.sender` (the end user) into the pool's `sender` argument. [4](#0-3) 

**Contrast with DepositAllowlistExtension (not affected):**

`DepositAllowlistExtension.beforeAddLiquidity()` checks `owner`, which is an explicit argument that the liquidity adder correctly sets to the position owner (`msg.sender` or a caller-specified address). The deposit guard is not affected by this issue. [5](#0-4) 

---

### Impact Explanation

**Scenario A — Allowlist bypass (High):**

1. Pool admin deploys a curated pool with `SwapAllowlistExtension` and allowlists specific users (e.g., KYC'd addresses).
2. Admin also allowlists the router address so that allowlisted users can trade through the standard periphery interface — a natural and expected configuration.
3. Any non-allowlisted user calls `MetricOmmSimpleRouter.exactInputSingle()`. The router calls `pool.swap()` with `msg.sender = router`. The extension checks `allowedSwapper[pool][router]` → `true`. The swap executes.
4. The allowlist is completely bypassed for all users. The pool's curation policy is void.

**Scenario B — Broken router path for allowlisted users (Medium):**

If the admin does NOT allowlist the router, allowlisted users who attempt to trade through the router are blocked (the extension sees `sender = router`, which is not on the allowlist). The router — the primary user-facing interface — is unusable for the pool's intended participants.

Both scenarios represent broken core pool functionality. Scenario A is the higher-severity path because it silently voids the allowlist invariant the pool was designed to enforce.

---

### Likelihood Explanation

The trigger is a valid, unprivileged user action (calling the public router). The precondition — the admin allowlisting the router — is a reasonable operational step that any pool admin would take to make their curated pool usable through the standard periphery. The admin has no way to know this action opens the gate to all users, because the extension's documentation and interface imply it gates by swapper identity. The bug is latent in every `SwapAllowlistExtension`-protected pool that uses the router.

---

### Recommendation

The extension must check the **end user's identity**, not the immediate caller of `pool.swap()`. Two approaches:

1. **Preferred:** Require that swaps through the router pass the original user's address in `extensionData`, and have the extension decode and check that address. The router already forwards `extensionData` per-hop.
2. **Alternative:** The pool could forward the original user's address as a separate field (analogous to how `owner` is separate from `sender` in the liquidity path), so extensions can distinguish the economic actor from the routing intermediary.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Admin also allowlists the router so allowedUser can trade via periphery.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
swapExtension.setAllowedToSwap(address(pool), allowedUser, true);

// Attack: bannedUser routes through the router.
vm.prank(bannedUser);
// bannedUser approves router for tokenIn, then:
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    recipient: bannedUser,
    tokenIn: address(token1),
    zeroForOne: false,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: type(uint128).max,
    deadline: block.timestamp,
    extensionData: ""
}));
// Extension checks allowedSwapper[pool][router] == true → swap succeeds.
// bannedUser has bypassed the allowlist.
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
