### Title
`SwapAllowlistExtension` checks `sender` (the router) instead of the actual user, allowing any user to bypass the swap allowlist when the router is allowlisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` parameter, which is `msg.sender` of the pool's `swap()` call. When a user routes through `MetricOmmSimpleRouter`, `sender` is the router's address, not the actual user's address. If the router is allowlisted (a natural admin action to enable periphery-based swaps), every non-allowlisted user can bypass the curated pool's swap gate by routing through the router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs its identity check on `sender`:

```solidity
// SwapAllowlistExtension.sol:37-39
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
``` [1](#0-0) 

`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // ← whoever called pool.swap()
  recipient,
  zeroForOne,
  amountSpecified,
  priceLimitX64,
  packedSlot0Initial,
  bidPriceX64,
  askPriceX64,
  extensionData
);
``` [2](#0-1) 

When a user calls `MetricOmmSimpleRouter`, the router is `msg.sender` to the pool. The extension therefore checks whether the **router** is allowlisted, never the actual user.

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly ignores `sender` and checks `owner` — the actual LP position owner:

```solidity
// DepositAllowlistExtension.sol:32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
``` [3](#0-2) 

The two extensions are architecturally inconsistent: the deposit guard keys on the economic actor (`owner`); the swap guard keys on the immediate caller (`sender`/router).

---

### Impact Explanation

A pool admin who deploys a curated pool with `SwapAllowlistExtension` and then allowlists `MetricOmmSimpleRouter` as a trusted periphery (the natural step to let users trade through the standard UI) inadvertently opens the gate to every address. Any non-allowlisted user can call `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`, the router forwards the call to the pool with `msg.sender = router`, the extension sees the allowlisted router address, and the swap proceeds. The curated pool's entire access-control policy is silently nullified. This is a direct loss of curation integrity and, in pools where the allowlist enforces KYC, regulatory compliance, or LP-protection boundaries, a direct loss of LP principal protection.

---

### Likelihood Explanation

The trigger is a standard, expected admin action: allowlisting the official periphery router so that users can interact through the supported UI. Any pool that (a) configures `SwapAllowlistExtension` and (b) allowlists the router — a combination that is both natural and documented as the supported flow — is fully exposed. No attacker privilege is required beyond calling the public router.

---

### Recommendation

Replace the `sender` check with the actual user identity. Two options:

1. **Check `recipient`** if the pool's swap semantics guarantee `recipient` is the economic beneficiary (simplest fix, but `recipient` can be a contract in multi-hop routes).
2. **Pass the real user through `extensionData`** and have the router encode `msg.sender` there; the extension decodes and checks it. This is the robust solution and mirrors how `owner` is threaded through `addLiquidity`.

The deposit allowlist's pattern — ignoring `sender` and checking the explicit economic-actor parameter — should be the model for the swap allowlist as well.

---

### Proof of Concept

1. Pool admin deploys pool with `SwapAllowlistExtension`; allowlists `alice` only.
2. Pool admin calls `setAllowedToSwap(pool, router, true)` to enable periphery access.
3. `bob` (not allowlisted) calls `MetricOmmSimpleRouter.exactInputSingle(pool, ...)`.
4. Router calls `pool.swap(recipient=bob, ...)` with `msg.sender = router`.
5. Pool calls `_beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router]` → `true` → no revert.
7. `bob`'s swap executes in the curated pool despite never being allowlisted.

Direct call by `bob` to `pool.swap()` would correctly revert (`allowedSwapper[pool][bob]` is `false`), confirming the bypass is router-specific. [1](#0-0) [2](#0-1) [3](#0-2)

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
