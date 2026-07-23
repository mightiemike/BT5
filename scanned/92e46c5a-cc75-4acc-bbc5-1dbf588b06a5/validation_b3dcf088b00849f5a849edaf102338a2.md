### Title
`SwapAllowlistExtension` gates the router address instead of the end user, allowing any unprivileged user to bypass the swap allowlist via `MetricOmmSimpleRouter` - (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which is the direct `msg.sender` of `pool.swap()`. When a swap is routed through `MetricOmmSimpleRouter`, `sender` is the **router's address**, not the end user's address. If the pool admin allowlists the router to enable router-mediated swaps for legitimate users, every unprivileged user can bypass the curated pool's access control by routing through the same public router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it unchanged to every configured extension: [1](#0-0) 

`ExtensionCalling._beforeSwap` encodes that `sender` verbatim into the extension call: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap()` on the user's behalf. At that point `msg.sender` of `pool.swap()` is the **router contract**, so `sender` delivered to the extension is the router's address — not the end user's address. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][endUser]`.

This creates two symmetric failure modes:

1. **Allowlist bypass (critical path):** The pool admin allowlists the router address so that legitimate users can trade via the router. Because the router is a public, permissionless contract, any unprivileged user can call `exactInputSingle()` and the extension will see `sender = router` → allowed. The curated pool's access control is completely bypassed.

2. **Allowlist false-block:** The pool admin allowlists individual user addresses (the intended design). Legitimate users who route through the router are blocked because the extension sees `sender = router` → not allowed. Users are forced to call `pool.swap()` directly, which may not be the intended UX.

The `DepositAllowlistExtension` does **not** share this flaw — it ignores `sender` and checks `owner` (the position owner), which is the economically relevant actor regardless of who the `msg.sender` of `addLiquidity()` is: [4](#0-3) 

The swap allowlist has no equivalent `owner`-style separation; it only has `sender`.

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to a known set of addresses (e.g., KYC'd counterparties, whitelisted market makers) is fully bypassed the moment the router is allowlisted. Any anonymous user can trade against the pool's liquidity, extracting value from LPs who deposited under the assumption that only vetted counterparties could trade. This is a direct loss of LP principal through unauthorized adverse selection.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, documented user-facing entry point for swaps. Any pool that configures `SwapAllowlistExtension` and also wants to support router-mediated swaps (the normal UX) must allowlist the router, immediately opening the bypass to all users. The trigger requires no special privileges — any public user can call `exactInputSingle()`.

---

### Recommendation

The extension must gate on the **end user's identity**, not the intermediary's. Two viable approaches:

1. **Pass original caller through `extensionData`:** Have the router encode the original `msg.sender` into `extensionData` and have `SwapAllowlistExtension.beforeSwap` decode and check it. This requires a convention between the router and the extension.

2. **Check both `sender` and a decoded originator:** Allow the extension to accept either a directly-allowlisted `sender` or a verified originator extracted from `extensionData`, so direct calls and router calls are both handled correctly.

The `DepositAllowlistExtension` pattern — checking `owner` rather than `sender` — is the correct model: the economically relevant actor (the one who receives LP shares / executes the trade) must be the gated identity.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured on `BEFORE_SWAP_ORDER`.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable router-mediated swaps.
3. Pool admin does **not** call `setAllowedToSwap(pool, attacker, true)`.
4. Attacker (unprivileged address) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
5. Router calls `pool.swap(...)` with `msg.sender = router`.
6. `_beforeSwap` passes `sender = router` to the extension.
7. Extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Attacker's swap executes against the curated pool despite never being allowlisted. [3](#0-2) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
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

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
