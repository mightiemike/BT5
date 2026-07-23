### Title
SwapAllowlistExtension Checks Direct Pool Caller Instead of End User, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` gates swaps by checking the `sender` parameter, which is the direct `msg.sender` of `pool.swap()`. When `MetricOmmSimpleRouter` mediates a swap, `sender` is the router address, not the end user. A pool admin who allowlists the router (necessary for any allowlisted user to trade via the router) simultaneously opens the gate to every non-allowlisted user, completely defeating the curation policy.

---

### Finding Description

`MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, recipient, ...)`, forwarding its own `msg.sender` as the `sender` argument. [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that value verbatim and dispatches it to every configured extension. [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the direct pool caller. [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap()`, the pool's `msg.sender` is the router, so `sender` delivered to the extension is the router address — not the originating user. The extension therefore checks whether the **router** is allowlisted, not whether the **end user** is allowlisted.

This creates an irresolvable dilemma for the pool admin:

| Admin choice | Effect |
|---|---|
| Do **not** allowlist the router | Allowlisted users cannot trade via the router at all |
| **Allowlist the router** | Every non-allowlisted user can bypass the gate by routing through the router |

There is no configuration that simultaneously permits allowlisted users to use the router and blocks non-allowlisted users from doing the same.

Note the contrast with `DepositAllowlistExtension.beforeAddLiquidity()`, which correctly checks the `owner` parameter (the economic actor), not `msg.sender` (the liquidity adder contract). [4](#0-3) 

The swap extension checks the wrong identity; the deposit extension checks the right one.

---

### Impact Explanation

A pool admin deploys a curated pool with `SwapAllowlistExtension` to restrict trading to a known set of counterparties (e.g., to protect LP funds from informed flow or MEV). Once the admin allowlists the router so that approved users can trade via the standard periphery path, every non-approved address can immediately bypass the restriction by calling `MetricOmmSimpleRouter.exactInputSingle` or `exactInput`. The allowlist provides zero protection against router-mediated swaps, exposing LP principal to the exact adversarial flow the curation was meant to block. This is a direct loss-of-LP-funds path and an admin-boundary break.

---

### Likelihood Explanation

The trigger is a normal, supported user action (routing through the official periphery router). The only precondition is that the pool admin allowlists the router — a step any admin who wants approved users to access the router must take. The admin's mental model ("allowlisting the router lets my approved users trade via the router") is incorrect; the actual effect is "allowlisting the router lets everyone trade via the router." No privileged access, no malicious setup, and no non-standard tokens are required.

---

### Recommendation

Pass the originating user's address through the router to the pool, and have the pool forward it as `sender` to extensions. One approach: the router encodes the original `msg.sender` inside `extensionData` and the extension decodes it; a cleaner approach is to add an explicit `originator` field to the swap call that the pool forwards to hooks. Alternatively, mirror the deposit-allowlist pattern: gate on the `recipient` (the economic beneficiary) rather than the direct pool caller, or require the router to be a trusted forwarder that attests the real user identity.

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin allowlists `alice` (trusted trader) and the `MetricOmmSimpleRouter` address so that `alice` can trade via the router.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. The router calls `pool.swap(recipient=bob, ...)`. Pool's `msg.sender` = router.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` (admin allowlisted the router in step 2).
7. The check passes. `bob`'s swap executes against LP funds despite `bob` never being allowlisted. [5](#0-4) [6](#0-5)

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
