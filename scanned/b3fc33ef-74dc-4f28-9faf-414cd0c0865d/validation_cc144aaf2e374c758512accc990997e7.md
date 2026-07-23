### Title
SwapAllowlistExtension Gates on Router Address Instead of Actual Swapper, Enabling Full Allowlist Bypass via MetricOmmSimpleRouter — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap()` call. When a user swaps through `MetricOmmSimpleRouter`, `msg.sender` of the pool call is the router, not the end user. If the pool admin allowlists the router (the only way to support router-mediated swaps for their users), every unprivileged address can bypass the allowlist by routing through the router.

---

### Finding Description

**Call chain:**

```
User → MetricOmmSimpleRouter.exact*()
         → MetricOmmPool.swap(recipient, ...) [msg.sender = router]
              → _beforeSwap(msg.sender=router, recipient=user, ...)
                   → SwapAllowlistExtension.beforeSwap(sender=router, ...)
                        → allowedSwapper[pool][router]  ← checked, NOT the user
```

In `MetricOmmPool.swap`, the pool passes `msg.sender` (the router) as `sender` to the extension dispatcher: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is the router: [3](#0-2) 

For a curated pool to support any router-mediated swap at all, the pool admin must call `setAllowedToSwap(pool, router, true)`. Once the router is allowlisted, the check `allowedSwapper[pool][router]` passes for **every** caller of the router, regardless of whether that caller is individually allowlisted.

The `DepositAllowlistExtension` does not share this flaw because it ignores `sender` and gates on `owner`, which is the economically relevant identity for liquidity positions: [4](#0-3) 

---

### Impact Explanation

A pool deploying `SwapAllowlistExtension` intends to restrict swaps to a curated set of addresses (e.g., KYC-verified users, institutional counterparties, or whitelisted market makers). Once the pool admin allowlists the router to support normal user flows, the allowlist is entirely defeated: any address can call `MetricOmmSimpleRouter.exact*()` and the extension will approve the swap because it sees the router's address, which is allowlisted. This is a direct loss of the pool's curation guarantee and constitutes broken core pool functionality — the allowlist guard silently fails open for all router-mediated swaps.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary user-facing swap entry point. Any pool admin who wants their allowlisted users to be able to use the standard router must allowlist the router address. This is the expected operational path, not an edge case. The bypass is therefore reachable on every curated pool that supports router-mediated swaps, triggered by any unprivileged user with no special access or setup.

---

### Recommendation

The extension must gate on the **economically relevant actor** — the address that initiated the swap and will pay for it — not the intermediate dispatcher. Two sound approaches:

1. **Pass the original caller through the router.** Have `MetricOmmSimpleRouter` pass the real `msg.sender` as an explicit parameter (e.g., inside `extensionData` or a dedicated field) and have the allowlist extension read that value. This requires a coordinated interface change.

2. **Gate on `recipient` instead of `sender` when the sender is a known router.** Less clean but avoids interface changes; still requires the extension to know which addresses are routers.

The cleanest fix mirrors how `DepositAllowlistExtension` handles the analogous problem: identify the actor that the pool's economic action is attributed to and check that identity, not the intermediary dispatcher.

---

### Proof of Concept

```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension configured
  allowedSwapper[pool][alice] = true          // alice is the intended grantee
  allowedSwapper[pool][router] = true         // admin must set this to support router swaps
  allowedSwapper[pool][attacker] = false      // attacker is NOT allowlisted

Attack:
  attacker calls MetricOmmSimpleRouter.exactInput(pool, ...)
    → pool.swap(recipient=attacker, ...) [msg.sender = router]
    → _beforeSwap(sender=router, ...)
    → SwapAllowlistExtension.beforeSwap(sender=router, ...)
    → allowedSwapper[pool][router] == true  → PASSES
    → swap executes for attacker

Result:
  attacker swaps on a pool they are explicitly not allowlisted for.
  The allowlist guard is completely bypassed.
``` [5](#0-4)

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
