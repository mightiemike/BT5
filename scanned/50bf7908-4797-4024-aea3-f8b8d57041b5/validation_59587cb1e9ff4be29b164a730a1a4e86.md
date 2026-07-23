### Title
SwapAllowlistExtension Gates on Router Address Instead of Economic Actor, Enabling Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool populates with `msg.sender` of the `pool.swap()` call. When `MetricOmmSimpleRouter` intermediates the swap, `sender` is the **router address**, not the end user. A pool admin who allowlists the router to enable router-based swaps for curated users inadvertently grants every user on the network the ability to bypass the allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to every registered extension: [1](#0-0) 

That value flows into `ExtensionCalling._beforeSwap` and is forwarded verbatim to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`: [3](#0-2) 

When `MetricOmmSimpleRouter` calls `pool.swap()`, `msg.sender` inside the pool is the **router contract**, so the extension evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][end_user]`. The extension has no visibility into who initiated the call through the router.

The pool's `swap()` function has no explicit `sender` parameter; the only identity the pool can report is `msg.sender`: [4](#0-3) 

This creates two broken states:

| Admin configuration | Outcome |
|---|---|
| Allowlists individual users, not the router | Allowlisted users **cannot** swap through the router (broken core flow) |
| Allowlists the router to enable router-based swaps | **All** users bypass the allowlist through the router |

The second state is the fund-impacting path: the allowlist is silently nullified for every user who routes through the supported periphery contract.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of counterparties loses that restriction entirely once the router is allowlisted. Any unprivileged user can call `MetricOmmSimpleRouter` and trade against the pool's liquidity without authorization. LP funds are exposed to toxic flow or policy-violating counterparties that the pool admin explicitly intended to exclude.

This matches the allowed impact gate: **"Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows"** and **"allowlist bypass through a public router path."**

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint in the periphery layer. A pool admin who configures `SwapAllowlistExtension` and wants allowlisted users to be able to use the router must allowlist the router — there is no other mechanism. This is a natural, expected configuration step, not an exotic edge case. The bypass is therefore reachable on any curated pool that supports router-based access.

---

### Recommendation

The extension must gate on the **economic actor**, not the immediate pool caller. Two viable approaches:

1. **Pass the originating user through `extensionData`**: The router encodes the actual `msg.sender` (the end user) into `extensionData` before calling `pool.swap()`. The extension decodes and verifies that address against the allowlist. The pool admin must trust the router to populate this field honestly, which is acceptable for a factory-registered periphery contract.

2. **Add an explicit `originator` parameter to `pool.swap()`**: The pool accepts an `originator` address alongside `recipient`, passes it to extensions, and the extension checks `allowedSwapper[pool][originator]`. The router populates `originator = msg.sender` (the end user).

---

### Proof of Concept

```
1. Pool admin deploys pool with SwapAllowlistExtension configured.
2. Admin calls swapExtension.setAllowedToSwap(pool, user1, true)
   — intending only user1 to trade.
3. Admin calls swapExtension.setAllowedToSwap(pool, router, true)
   — to let user1 reach the pool through the router.
4. user2 (not allowlisted) calls MetricOmmSimpleRouter.swap(...).
5. Router calls pool.swap(...) — pool records msg.sender = router.
6. Pool calls extension.beforeSwap(router, ...).
7. Extension evaluates: allowedSwapper[pool][router] == true → PASS.
8. user2 completes the swap on the curated pool without authorization.
```

The invariant **"A curated pool must enforce the same allowlist policy regardless of which supported public entrypoint reaches it"** is broken: `SwapAllowlistExtension` enforces the policy correctly for direct pool calls but fails open for every call routed through `MetricOmmSimpleRouter`. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-225)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());
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
