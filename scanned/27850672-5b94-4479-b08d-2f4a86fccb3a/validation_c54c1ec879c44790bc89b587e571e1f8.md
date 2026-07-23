### Title
`SwapAllowlistExtension.beforeSwap` gates the router address instead of the actual user, allowing any unprivileged caller to bypass the swap allowlist on curated pools - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension` is documented as gating "`swap` by swapper address, per pool." In practice it checks `sender`, which is `msg.sender` of `pool.swap()`. When a user enters through `MetricOmmSimpleRouter`, `msg.sender` of `pool.swap()` is the **router contract**, not the end user. If the pool admin allowlists the router address to enable router-mediated swaps for their curated users, every unprivileged address on-chain can bypass the allowlist by routing through the same public router.

---

### Finding Description

**Call chain that exposes the wrong actor:**

```
user → MetricOmmSimpleRouter.exactInputSingle(...)
         └─ pool.swap(recipient, ..., extensionData)   // msg.sender = router
               └─ _beforeSwap(msg.sender=router, ...)
                     └─ ExtensionCalling._beforeSwap(sender=router, ...)
                           └─ SwapAllowlistExtension.beforeSwap(sender=router, ...)
```

`MetricOmmPool.swap` passes `msg.sender` as `sender` to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool (correct key) and `sender` is the router (wrong actor): [3](#0-2) 

When a user calls the pool directly, `sender` = user → allowlist is enforced correctly. When the same user calls through `MetricOmmSimpleRouter.exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput`, `sender` = router → the allowlist checks the router's entry, not the user's entry. [4](#0-3) 

The pool admin who wants to support router-mediated swaps for their allowlisted users has only one option: `setAllowedToSwap(pool, router, true)`. The moment they do so, `allowedSwapper[pool][router] = true`, and the check at line 37 passes for **every** caller that routes through the router — including addresses the admin never intended to allowlist.

---

### Impact Explanation

A curated pool with `SwapAllowlistExtension` is designed to restrict trading to a specific set of addresses (e.g., KYC'd counterparties, institutional traders, or protocol-controlled addresses). Once the pool admin allowlists the router to support the standard periphery UX, the guard fails open for all router users:

- Any unprivileged address can call `MetricOmmSimpleRouter.exactInputSingle(pool, ...)` and trade in the pool.
- The oracle-anchored pool still executes at live bid/ask prices, so the unauthorized trader receives tokens at the pool's current quoted price. LP funds are exposed to adverse selection, MEV, and any other risk the allowlist was meant to prevent.
- The pool admin has no on-chain mechanism to simultaneously allowlist the router for their approved users and block it for everyone else, because the extension has no way to recover the original `tx.origin` or a signed permit from the router call.

This is a direct loss-of-curation-control impact: the allowlist invariant — "only approved addresses may swap" — is permanently broken for all router-mediated paths once the router is added.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint in `metric-periphery`. Any pool admin who deploys a curated pool and wants their allowlisted users to use the standard router will naturally call `setAllowedToSwap(pool, router, true)`. The bypass then requires no special knowledge: any user who observes the router is allowlisted can immediately exploit it. The trigger is fully unprivileged and requires a single public transaction.

---

### Recommendation

The extension must gate the economically relevant actor, not the intermediary. Two sound approaches:

1. **Check `sender` only for direct pool calls; require a signed permit or EIP-712 proof for router calls.** The router can forward a user-signed payload in `extensionData` that the extension verifies.

2. **Allowlist at the router level:** The router exposes `msg.sender` (the real user) in `extensionData` or a separate signed field, and the extension verifies that field against the allowlist instead of the raw `sender` argument.

At minimum, the NatSpec and admin documentation must warn that allowlisting the router address opens the pool to all router users, so pool admins do not make this mistake inadvertently.

---

### Proof of Concept

```solidity
// Setup: pool admin deploys curated pool with SwapAllowlistExtension
// and allowlists the router so that approved users can use the standard UX.
swapExtension.setAllowedToSwap(address(pool), address(router), true);
// Admin intends only their approved users to trade, but the router is now the gated identity.

// Attack: any unprivileged address bypasses the allowlist via the router.
address attacker = makeAddr("attacker");
deal(address(token0), attacker, 1_000e18);
vm.startPrank(attacker);
token0.approve(address(router), type(uint256).max);

// Direct pool call would revert: allowedSwapper[pool][attacker] == false
// vm.expectRevert(NotAllowedToSwap.selector);
// pool.swap(attacker, true, 1000, type(uint128).max, "", "");

// Router call succeeds: allowedSwapper[pool][router] == true → check passes
router.exactInputSingle(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool:            address(pool),
        tokenIn:         address(token0),
        recipient:       attacker,
        deadline:        block.timestamp + 1,
        amountIn:        1_000e18,
        amountOutMinimum: 0,
        zeroForOne:      true,
        priceLimitX64:   0,
        extensionData:   ""
    })
);
// Attacker successfully swaps in the curated pool — allowlist bypassed.
vm.stopPrank();
```

The `sender` argument received by `SwapAllowlistExtension.beforeSwap` is `address(router)`, which is allowlisted, so line 37 passes and the unauthorized swap executes. [5](#0-4) [6](#0-5)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-177)
```text
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
