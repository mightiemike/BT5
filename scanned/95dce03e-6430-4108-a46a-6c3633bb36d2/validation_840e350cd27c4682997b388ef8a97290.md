Based on my analysis of the codebase, I can identify a concrete analog to the double-entry-point bypass in the Metric OMM extension system.

---

### Title
`SwapAllowlistExtension` gates the router address instead of the actual swapper, enabling allowlist bypass via `MetricOmmSimpleRouter` — (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` — the **direct caller of `pool.swap()`**. When a user routes through `MetricOmmSimpleRouter`, the pool passes `sender = router_address` to the extension. If the router is allowlisted (which is required for any router-mediated swap to work on a curated pool), every unprivileged user can bypass the allowlist by routing through the public router contract.

### Finding Description

In `MetricOmmPool.swap`, the pool passes its own `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` then forwards this value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` checks that `sender` (the direct pool caller) is allowlisted: [3](#0-2) 

The allowlist is keyed `allowedSwapper[pool][sender]`. When `MetricOmmSimpleRouter` calls `pool.swap(...)`, `sender` is the **router address**, not the originating user. The extension has no way to distinguish "allowlisted user going through the router" from "non-allowlisted user going through the router."

This creates an irresolvable dilemma for the pool admin:

| Router allowlisted? | Effect |
|---|---|
| No | All router-mediated swaps blocked, even for allowlisted users |
| Yes | All users bypass the allowlist via the router |

The allowlist mapping is: [4](#0-3) 

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to gate specific counterparties (e.g., KYC-verified traders, institutional partners) cannot enforce that policy for router-mediated swaps. Any unprivileged user can bypass the allowlist by calling `MetricOmmSimpleRouter`, which is a public permissionless contract. This breaks the core pool functionality the allowlist was designed to provide and constitutes an admin-boundary break: the pool admin's configured access control is bypassed by an unprivileged path. If the pool's risk model depends on counterparty curation (e.g., to prevent adverse selection against LP positions), the bypass directly threatens LP principal.

### Likelihood Explanation

The likelihood is **medium-high**. A pool admin who wants allowlisted users to be able to use the router (the standard periphery entrypoint) must allowlist the router address. This is the natural configuration. The admin has no mechanism to allowlist the router for specific users only — the check is binary on the router address. The `MetricOmmSimpleRouter` is a public contract callable by anyone. [5](#0-4) 

### Recommendation

The extension must check the **economically relevant actor**, not the intermediary. Two approaches:

1. **Pass original user in `extensionData`**: Have the router encode the originating user's address in `extensionData` and have the extension verify it (with the router's own signature or a trusted-forwarder pattern).
2. **Check `recipient` instead of `sender`**: If the pool's curation intent is to gate who receives output tokens, check the `recipient` argument instead. However, this changes the semantics.
3. **Require direct pool interaction for allowlisted pools**: Document that pools using `SwapAllowlistExtension` must not allowlist the router, and the router must not be used for such pools.

### Proof of Concept

1. Pool admin deploys pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to allow router-mediated swaps for their allowlisted users.
3. Non-allowlisted attacker calls `MetricOmmSimpleRouter.exactInput(...)` targeting the curated pool.
4. Router calls `pool.swap(recipient, zeroForOne, amount, priceLimit, callbackData, extensionData)` — pool sees `msg.sender = router`.
5. Pool calls `_beforeSwap(router, recipient, ...)` — extension receives `sender = router`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
7. Attacker successfully trades on a pool they were never authorized to access.

The analog to the double-entry-point bug is exact: just as TUSD's two addresses represent the same underlying balance but bypass deduplication, the router and the real user represent the same economic actor but the allowlist only sees one address — the wrong one. [3](#0-2)

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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-25)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
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
