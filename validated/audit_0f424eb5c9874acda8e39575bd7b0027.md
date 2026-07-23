### Title
`SwapAllowlistExtension` checks the router address instead of the actual swapper, allowing any user to bypass the per-user swap allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When a user routes through `MetricOmmSimpleRouter`, the pool sets `sender = msg.sender = router address`. The extension therefore checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. If the router is added to the allowlist (a natural operational step so that allowlisted users can use the router), the per-user allowlist is completely bypassed and any user can trade in the curated pool.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value unchanged as the first argument to the extension's `beforeSwap`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

When a user calls `MetricOmmSimpleRouter` (a supported periphery path), the router calls `pool.swap()` on the user's behalf. From the pool's perspective, `msg.sender` is the **router address**, so `sender = router`. The extension therefore evaluates `allowedSwapper[pool][router]` — not `allowedSwapper[pool][actual_user]`.

This creates two failure modes from the same root cause:

1. **Allowlist bypass**: If the pool admin adds the router to the allowlist (so that allowlisted users can trade via the router), the check degenerates to "is the router allowlisted?" — which is always true — and any non-allowlisted user can trade by routing through `MetricOmmSimpleRouter`.
2. **Allowlisted-user lockout**: If the router is not allowlisted, allowlisted users cannot use the router at all, breaking the intended UX of the curated pool.

The `DepositAllowlistExtension` does not share this flaw because it checks the `owner` parameter (an explicit argument to `addLiquidity`), not the implicit `msg.sender` chain: [4](#0-3) 

---

### Impact Explanation

A curated pool deploying `SwapAllowlistExtension` to restrict trading to KYC-approved or otherwise vetted addresses loses that restriction entirely once the router is allowlisted. Any unprivileged user can execute swaps against the pool's liquidity, draining LP assets at oracle-derived prices the pool was not intended to expose to arbitrary counterparties. This is a direct loss of LP principal and a complete curation failure.

---

### Likelihood Explanation

The router is a first-party, documented periphery contract. Pool admins who want allowlisted users to be able to use the router will naturally add the router to the allowlist. The bypass requires no special privileges, no flash loans, and no exotic token behavior — only a call through `MetricOmmSimpleRouter`. Likelihood is **high** for any curated pool that also permits router access.

---

### Recommendation

The extension must identify the **economic actor** — the address that initiated the trade and will bear its cost — not the intermediate caller. Two options:

**Option A** — Pass the original initiator through the router as an explicit argument and have the pool forward it. This requires a protocol-level change to the `swap` interface.

**Option B** — Check `tx.origin` as a fallback when `sender` is a known router. This is fragile and generally discouraged.

**Option C (preferred)** — Require that swaps through the router include the actual user address in `extensionData`, and have `SwapAllowlistExtension.beforeSwap` decode and check that address when `sender` is a recognized router. The pool already threads `extensionData` through to the extension: [5](#0-4) 

The cleanest long-term fix is to add an explicit `originator` field to the pool's `swap` call so the extension always receives the true economic actor regardless of routing depth.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` so that allowlisted users can trade via the router.
3. Non-allowlisted `attacker` calls `MetricOmmSimpleRouter.swap(...)` targeting the curated pool.
4. Router calls `pool.swap(recipient, ...)` — pool sets `sender = address(router)`.
5. Extension evaluates `allowedSwapper[pool][router]` → `true` → swap proceeds.
6. Attacker receives output tokens; LP bears the loss.

The allowlist check that was supposed to block step 3 never fires against the attacker's address. [6](#0-5)

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

**File:** metric-core/contracts/interfaces/extensions/IMetricOmmExtensions.sol (L50-60)
```text
  function beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) external returns (bytes4);
```
