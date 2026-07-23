### Title
SwapAllowlistExtension checks the router's address instead of the actual user's address, allowing any user to bypass the swap allowlist on curated pools — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed from the pool. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the end user. The extension therefore checks whether the **router** is allowlisted, not whether the **actual user** is allowlisted. If the pool admin allowlists the router to enable router-mediated swaps, every user — including those explicitly excluded — can bypass the curated allowlist.

---

### Finding Description

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`_beforeSwap` forwards that value as the first positional argument to every configured extension. `SwapAllowlistExtension.beforeSwap` receives it as `sender` and checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

Here `msg.sender` is the pool (the extension's caller) and `sender` is whoever called `pool.swap`. When the user goes through `MetricOmmSimpleRouter`, the router calls `pool.swap(...)`, so `sender` is the **router's address**. The check becomes `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

The deposit-side extension does not share this flaw: `DepositAllowlistExtension.beforeAddLiquidity` ignores the first (sender) argument and gates on `owner`, which is the position owner supplied by the caller — correctly preserved through the liquidity adder path. [3](#0-2) 

The swap extension has no equivalent correction.

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` intends to restrict trading to a specific set of addresses. To allow those addresses to also use the public router, the admin must add the router to the allowlist (`allowedSwapper[pool][router] = true`). Once the router is allowlisted, **any address** — including those the admin explicitly excluded — can call `MetricOmmSimpleRouter.exact*` and have the pool accept the swap, because the extension only sees the router's address. The allowlist is completely ineffective for router-mediated swaps. Disallowed users gain full access to the pool's liquidity at oracle-anchored prices, defeating the curation policy and potentially draining LP value through adversarial trading on a pool that was designed to be restricted.

---

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the standard, publicly documented user-facing entry point. Any user who reads the periphery contracts can discover this path. The only precondition is that the pool admin has allowlisted the router (a near-certain operational step for any pool that intends to support normal UX). No privileged access, no special tokens, and no malicious setup are required.

---

### Recommendation

The extension must gate on the **end user's identity**, not the intermediary's. Two complementary fixes:

1. **Pass the original caller through the router.** The router should forward the original `msg.sender` as the `recipient` or via `extensionData`, and the extension should read that value instead of the raw `sender` argument.

2. **Alternatively, check `sender` only when it is not a known router, and require the router to attest the real user in `extensionData`.** The extension can decode a signed or ABI-encoded user address from `extensionData` when `sender` is a recognized periphery contract.

The deposit allowlist already demonstrates the correct pattern: it ignores the intermediary (`msg.sender` of `addLiquidity`) and gates on `owner`, which is the economically relevant identity. The swap allowlist should adopt the same principle.

---

### Proof of Concept

1. Deploy a pool with `SwapAllowlistExtension` configured.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — Alice is the only allowed swapper.
3. Pool admin calls `setAllowedToSwap(pool, router, true)` — router is allowlisted so Alice can use it.
4. Bob (not on the allowlist) calls `MetricOmmSimpleRouter.exactInput(...)` targeting the pool.
5. The router calls `pool.swap(...)` with `msg.sender = router`.
6. `_beforeSwap` passes `sender = router` to `SwapAllowlistExtension.beforeSwap`.
7. The extension evaluates `allowedSwapper[pool][router] == true` → passes.
8. Bob's swap executes successfully despite being explicitly excluded from the allowlist. [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L224-241)
```text
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
