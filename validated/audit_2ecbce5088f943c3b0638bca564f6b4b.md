Based on my investigation of the repository, I found a genuine wrong-actor binding issue in the `SwapAllowlistExtension` that is analogous to the external bug's guard-bypass pattern.

---

### Title
`SwapAllowlistExtension` Checks Router Address Instead of End User, Allowing Allowlist Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

The `SwapAllowlistExtension.beforeSwap` hook checks the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router address, not the end user. A pool admin who allowlists the router to enable router-based swaps inadvertently grants every user — including those not individually allowlisted — the ability to bypass the per-user swap restriction. Conversely, if the router is not allowlisted, individually allowlisted users cannot swap through the router at all. The allowlist is rendered either universally bypassed or universally broken for router-mediated flows.

### Finding Description

`SwapAllowlistExtension.beforeSwap` gates on the `sender` argument:

```solidity
// SwapAllowlistExtension.sol line 37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [1](#0-0) 

The pool's `swap()` passes `msg.sender` (the immediate caller) as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol lines 230-240
_beforeSwap(
  msg.sender,   // ← this becomes `sender` in the extension
  recipient,
  zeroForOne,
  ...
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()`, so `msg.sender` seen by the pool — and forwarded as `sender` to the extension — is the **router address**, not the end user.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly checks `owner` (the actual position beneficiary), ignoring the payer/caller:

```solidity
// DepositAllowlistExtension.sol line 32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    ...
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
``` [3](#0-2) 

This inconsistency means the two sibling extensions apply fundamentally different actor-binding semantics: deposits gate the actual user; swaps gate the router.

### Impact Explanation

A pool admin who configures `SwapAllowlistExtension` to restrict swaps to a curated set of addresses faces a forced dilemma:

- **If the router is allowlisted** (required for any router-based swap to work): every user, regardless of individual allowlist status, can bypass the restriction by routing through `MetricOmmSimpleRouter`. The curated pool's swap policy is entirely nullified for router flows.
- **If the router is not allowlisted**: individually allowlisted users cannot use the router at all, breaking the primary supported swap path.

On a curated pool (e.g., restricted to specific market makers or KYC'd counterparties), unauthorized users trading at oracle prices can extract value from LPs, constituting a direct loss of LP principal. This matches the "Admin-boundary break: factory/oracle role checks are bypassed by an unprivileged path" impact gate.

### Likelihood Explanation

The `MetricOmmSimpleRouter` is the primary supported swap entrypoint documented in the periphery. Any pool that enables `SwapAllowlistExtension` and also expects users to use the router will encounter this condition. The pool admin has no in-protocol mechanism to enforce per-user swap restrictions through the router — the flaw is structural.

### Recommendation

`SwapAllowlistExtension.beforeSwap` should check the `recipient` or an explicit "originating user" field rather than `sender`. Alternatively, the pool should forward the original EOA through the callback context (similar to how `owner` is forwarded for deposits), and the extension should gate on that identity. The fix must make the swap allowlist check the same actor that the deposit allowlist checks — the actual economic beneficiary/initiator — not the intermediate router contract.

### Proof of Concept

1. Pool admin deploys a pool with `SwapAllowlistExtension` configured.
2. Pool admin allowlists `alice` as an approved swapper: `setAllowedToSwap(pool, alice, true)`.
3. Pool admin also allowlists the router (necessary for router-based swaps): `setAllowedToSwap(pool, router, true)`.
4. `bob` (not allowlisted) calls `router.exactInputSingle(...)`.
5. Router calls `pool.swap(recipient=bob, ...)` — `msg.sender` to the pool is `router`.
6. Pool calls `extension.beforeSwap(sender=router, ...)`.
7. Extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
8. `bob` successfully swaps against the curated pool despite not being individually allowlisted. [1](#0-0) [4](#0-3) [3](#0-2)

### Citations

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
