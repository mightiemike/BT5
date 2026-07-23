### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original Swapper, Enabling Full Allowlist Bypass via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is `msg.sender` of the pool — the router contract — not the original user. When a pool admin allowlists the router to enable router-mediated swaps, every unpermissioned user can bypass the curated allowlist by routing through `MetricOmmSimpleRouter`.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exactInputSingle()
         → pool.swap(recipient, ...)          // msg.sender = router
              → _beforeSwap(msg.sender=router, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → checks allowedSwapper[pool][router]   ← WRONG ACTOR
```

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` (the router) as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

The router never forwards the original caller's identity to the pool: [4](#0-3) 

---

### Impact Explanation

Two mutually exclusive broken states result from this wrong-actor binding:

**State A — Router is allowlisted (pool admin enables router-mediated swaps):**
Every user, regardless of allowlist status, can bypass the curated gate by calling `MetricOmmSimpleRouter.exactInputSingle/exactInput/exactOutputSingle/exactOutput`. The extension sees `sender = router`, which is allowlisted, and passes. The allowlist provides zero protection. Any user can trade on a pool intended to be restricted to KYC'd or permissioned addresses, causing direct loss of curation integrity and potential regulatory/fund-safety failure for the pool's LPs.

**State B — Router is not allowlisted:**
Allowlisted users cannot use the router at all. Their own address is allowlisted, but the extension sees the router's address and reverts with `NotAllowedToSwap`. Core swap functionality is broken for the intended user set on every allowlisted pool.

State A is the fund-impacting path: it is the natural operational configuration (pool admin allowlists the router to make the pool usable), and it renders the allowlist completely inert.

---

### Likelihood Explanation

- The `MetricOmmSimpleRouter` is the primary user-facing swap entrypoint in the periphery; pool admins are expected to allow it.
- Allowlisting the router is the only way to let allowlisted users trade via the router, so pool admins will do it.
- Once the router is allowlisted, the bypass requires zero privilege — any EOA can call `exactInputSingle` with no special setup.
- No existing guard in the extension or pool prevents this path.

---

### Recommendation

The extension must check the economically relevant actor, not the intermediary. Two approaches:

1. **Pass original caller through the router:** The router stores `msg.sender` in transient storage (already done for the payer context) and the pool reads it as the canonical `sender` for extension purposes. This requires a protocol-level convention.

2. **Check `sender` in the extension against the pool-keyed allowlist using the original user:** Modify `SwapAllowlistExtension.beforeSwap` to accept and verify the original user identity, which the router must supply explicitly (e.g., via `extensionData`). This is the simpler fix without changing the core pool interface.

The simplest correct fix is to have the router pass the original `msg.sender` inside `extensionData` and have the extension decode and verify it, or to change the pool's `swap` signature to accept an explicit `swapper` address that the router populates with `msg.sender`.

---

### Proof of Concept

```solidity
// Setup: pool with SwapAllowlistExtension; only `allowedUser` is allowlisted.
// Pool admin also allowlists the router so allowedUser can trade via router.
extension.setAllowedToSwap(pool, allowedUser, true);
extension.setAllowedToSwap(pool, address(router), true); // required for router to work

// Attack: bannedUser bypasses allowlist via router
vm.prank(bannedUser);
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    recipient: bannedUser,
    zeroForOne: true,
    amountIn: 1000,
    amountOutMinimum: 0,
    priceLimitX64: 0,
    tokenIn: token0,
    deadline: block.timestamp,
    extensionData: ""
}));
// ✅ Succeeds — extension checked allowedSwapper[pool][router] = true
// bannedUser traded on a curated pool without being allowlisted
```

The `beforeSwap` hook receives `sender = address(router)`, which is allowlisted, so the check passes for `bannedUser`. [5](#0-4) [6](#0-5) [7](#0-6)

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
