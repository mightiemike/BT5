### Title
`SwapAllowlistExtension` Checks Router Address Instead of Actual Swapper, Allowing Any User to Bypass Per-User Swap Allowlist via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which is `msg.sender` of `pool.swap()`. When users route through `MetricOmmSimpleRouter`, `sender` is the **router address**, not the actual end user. If the pool admin allowlists the router to enable router-based swaps for their intended users, any unprivileged user can bypass the per-user swap allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks whether `sender` (the direct pool caller) is allowlisted for the calling pool: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle`, the router is the direct caller of `pool.swap()`: [4](#0-3) 

So `sender` received by the extension is the **router address**, not the actual end user (`msg.sender` of the router call). The extension has no visibility into who initiated the router call.

A pool admin who wants their allowlisted users to be able to use the router must allowlist the router contract itself. Once the router is allowlisted, the check `allowedSwapper[pool][router]` returns `true` for every swap routed through it, regardless of who the actual end user is. Any unprivileged address can then bypass the per-user allowlist by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` on the router.

The same structural flaw applies to multi-hop `exactInput`, where intermediate hops also use `msg.sender = router`: [5](#0-4) 

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` is intended to restrict trading to a specific set of counterparties (e.g., KYC-verified institutions, whitelisted market makers). Once the router is allowlisted to support those users, the guard collapses to a single bit: "is the router allowlisted?" Any retail or adversarial user can trade against the pool's LPs by routing through the public router. LPs face unexpected counterparties and adverse selection they explicitly opted out of, resulting in direct LP value loss. The allowlist — the only mechanism protecting LP capital from unwanted counterparties — is rendered ineffective.

**Severity: Medium.** The bypass is fully unprivileged (any user can call the router). The admin action that enables it (allowlisting the router) is a natural and expected operational step, not a malicious or obviously incorrect one. The economic harm is LP loss from adverse selection rather than direct token theft.

---

### Likelihood Explanation

Any pool that (a) deploys `SwapAllowlistExtension` for per-user gating and (b) also allowlists the router to support standard periphery flows is vulnerable. Both conditions are independently reasonable and likely to co-occur in production. The bypass requires no special privileges, no flash loans, and no contract deployment — a plain EOA calling the public router suffices.

---

### Recommendation

The extension must check the **economically relevant actor**, not the direct pool caller. Two viable approaches:

1. **Pass the original initiator through the router.** The router can encode the original `msg.sender` in `extensionData` and the extension can decode and verify it. This requires a convention between the router and the extension.

2. **Check `sender` AND require `sender != router` (or any known intermediary).** Reject swaps where `sender` is a known public router unless the actual user is separately verified. This is fragile as new routers are deployed.

3. **Document the incompatibility explicitly.** If per-user gating is required, the pool must be accessed directly (not through the router), and the documentation must make this clear so admins do not allowlist the router.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension (BEFORE_SWAP_ORDER = extension 1).
  - Pool admin allowlists the router: swapExtension.setAllowedToSwap(pool, router, true).
  - Pool admin does NOT allowlist Alice (0xAlice).

Attack:
  1. Alice calls router.exactInputSingle({pool: pool, recipient: Alice, ...}).
  2. Router calls pool.swap(Alice, zeroForOne, amount, priceLimit, "", extensionData)
     → msg.sender of pool.swap() = router.
  3. _beforeSwap(sender=router, ...) → SwapAllowlistExtension.beforeSwap(sender=router, ...).
  4. Check: allowedSwapper[pool][router] == true → passes.
  5. Alice's swap executes successfully despite not being individually allowlisted.

Expected: revert NotAllowedToSwap().
Actual:   swap succeeds; Alice trades against pool LPs.
``` [6](#0-5) [7](#0-6)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
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
```
