### Title
`SwapAllowlistExtension` checks the router/intermediary address as `sender` instead of the actual end-user, enabling allowlist bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap()` is designed to gate swaps by the actual swapper's address. However, the `sender` value it receives is `msg.sender` of `MetricOmmPool.swap()` — which is the router contract when swaps are routed through `MetricOmmSimpleRouter`, not the end-user. A pool admin who allowlists the router to enable router-based swaps inadvertently grants every user access, defeating the per-user allowlist entirely.

---

### Finding Description

**Step 1 — Pool passes `msg.sender` as `sender` to the hook:**

In `MetricOmmPool.swap()`, the `sender` forwarded to `_beforeSwap` is unconditionally `msg.sender`: [1](#0-0) 

**Step 2 — `ExtensionCalling._beforeSwap` forwards it verbatim:** [2](#0-1) 

**Step 3 — `SwapAllowlistExtension.beforeSwap` checks `sender` (the router) against the allowlist:**

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is whoever called `pool.swap()` — the router, not the end-user. The actual user's address is never inspected.

**Contrast with `DepositAllowlistExtension`:** That extension checks `owner` (the second parameter), which callers of `addLiquidity` explicitly supply and which can be set to the real user. `SwapAllowlistExtension` has no equivalent — it relies solely on `sender`, which is structurally bound to the immediate caller. [4](#0-3) 

---

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict swaps to a specific set of counterparties (e.g., KYC'd addresses, whitelisted market makers) must also allowlist the router for those users to swap via the standard periphery path. Once the router is allowlisted, **every user** who routes through it passes the check, regardless of whether they are individually permitted. The per-user guard is completely bypassed. Unauthorized users can execute swaps against LP funds, extracting value at oracle-anchored prices from a pool that was intended to be restricted.

---

### Likelihood Explanation

This triggers under a natural and expected configuration: pool admin enables `SwapAllowlistExtension` to restrict access, then allowlists the router so that permitted users can use the standard periphery. Any user who discovers the router bypass can exploit it immediately with no special privileges. The router is a standard, documented periphery contract, so this path is reachable by any user.

---

### Recommendation

The actual end-user address must be conveyed to the extension through a channel that the router cannot spoof. Two options:

1. **Pass the real user via `extensionData`**: The router encodes the end-user address into `extensionData`; the extension decodes and checks it. This requires the router to be trusted to supply the correct address.
2. **Introduce a `swapper` parameter distinct from `sender`**: Add an explicit `swapper` field to the `beforeSwap` hook signature that the pool populates from a verified source (e.g., a transient-storage callback context set during the swap callback, analogous to how the reentrancy guard uses transient storage).

---

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Admin calls `setAllowedToSwap(pool, router, true)` — allowlisting the router so that permitted users can swap via periphery.
3. Admin does **not** call `setAllowedToSwap(pool, userC, true)` — `userC` is not a permitted counterparty.
4. `userC` calls `MetricOmmSimpleRouter.swap(...)`, which internally calls `pool.swap(recipient, ...)`.
5. Inside `pool.swap()`, `_beforeSwap(msg.sender=router, ...)` is called.
6. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` evaluates `allowedSwapper[pool][router] == true` → passes.
7. `userC`'s swap executes against LP funds. The allowlist guard was never applied to `userC`. [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-241)
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-41)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
  }

  function setAllowAllSwappers(address pool_, bool allowed) external onlyPoolAdmin(pool_) {
    allowAllSwappers[pool_] = allowed;
    emit AllowAllSwappersSet(pool_, allowed);
  }

  function isAllowedToSwap(address pool_, address swapper) external view returns (bool) {
    return allowAllSwappers[pool_] || allowedSwapper[pool_][swapper];
  }

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
