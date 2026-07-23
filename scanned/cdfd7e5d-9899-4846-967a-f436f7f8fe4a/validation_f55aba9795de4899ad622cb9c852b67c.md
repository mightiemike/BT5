### Title
`SwapAllowlistExtension.beforeSwap` Checks `sender` (Direct Caller) Instead of Actual User, Allowing Full Allowlist Bypass Through an Allowlisted Router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps on the `sender` parameter, which is `msg.sender` of the pool's `swap()` call — i.e., the direct caller (e.g., `MetricOmmSimpleRouter`). When a pool admin allowlists the router to permit authorized users to trade through it, every user who routes through that contract bypasses the per-user restriction, because the allowlist check resolves to the single router address rather than the individual user. This is structurally identical to the external bug: one entity (the router) is counted as the allowed party on behalf of arbitrarily many real users, collapsing a per-user guard into a per-router guard.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` reads:

```solidity
// SwapAllowlistExtension.sol L31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

`sender` is the first argument forwarded by the pool's internal `_beforeSwap` helper:

```solidity
// ExtensionCalling.sol L160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (sender, recipient, zeroForOne, amountSpecified, ...)
    )
);
```

The pool sets `sender = msg.sender` of its own `swap()` call. When `MetricOmmSimpleRouter` calls `pool.swap(recipient, ...)`, the pool's `msg.sender` is the router, so `sender = router`. The allowlist check therefore resolves to `allowedSwapper[pool][router]`, not `allowedSwapper[pool][actualUser]`.

**Contrast with `DepositAllowlistExtension`**, which correctly checks the actual beneficiary:

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

`owner` is the LP position owner (the actual user), not the router. The two sibling extensions apply fundamentally different identity models, and the swap extension uses the wrong one.

---

### Impact Explanation

A pool admin who deploys a restricted pool (e.g., a private institutional pool with tight spreads) and wants authorized users to trade through `MetricOmmSimpleRouter` must allowlist the router address. Once the router is allowlisted, **every address that calls the router** — including completely unauthorized users — passes the `beforeSwap` guard. The guard collapses from a per-user allowlist to a per-router allowlist. Unauthorized users can:

- Execute swaps in a pool intended to be private.
- If the pool offers subsidized or favorable pricing (tight bid/ask spread), drain LP value at below-market rates.
- Circumvent any KYC/compliance intent encoded in the allowlist.

This is a direct, fund-impacting consequence: LP principal is exposed to unauthorized counterparties at prices the LPs did not intend to offer to the general public.

---

### Likelihood Explanation

The trigger path is straightforward and requires no privileged access beyond what a normal user has:

1. Pool admin deploys a pool with `SwapAllowlistExtension` in `BEFORE_SWAP_ORDER`.
2. Pool admin allowlists `MetricOmmSimpleRouter` (a natural action to allow authorized users to trade through the standard router).
3. Any unauthorized user calls `MetricOmmSimpleRouter` → `pool.swap(...)`.
4. The pool passes `sender = router` to `beforeSwap`; the router is allowlisted; the check passes.
5. Unauthorized swap executes.

This is a realistic operational scenario. The pool admin's intent (allow authorized users via router) and the actual outcome (allow all users via router) diverge silently with no on-chain warning.

---

### Recommendation

Mirror the `DepositAllowlistExtension` pattern: check the actual user, not the intermediary. The pool's `swap()` interface exposes `recipient` as the swap beneficiary. Alternatively, the pool should pass the original `msg.sender` through a dedicated `swapper` field (separate from `recipient`) so the extension can check the true initiator. At minimum, document clearly that `SwapAllowlistExtension` is incompatible with router-mediated swaps for per-user access control, and add a check or a separate `swapper` parameter analogous to `owner` in the deposit path.

---

### Proof of Concept

```
Setup:
  - Pool configured with SwapAllowlistExtension in beforeSwap order.
  - Pool admin calls setAllowedToSwap(pool, router, true)
    (to allow authorized users to trade via MetricOmmSimpleRouter).
  - Unauthorized user (not on allowlist) calls:
      MetricOmmSimpleRouter.swap(pool, recipient=unauthorizedUser, ...)
        → pool.swap(recipient=unauthorizedUser, ...)
          → _beforeSwap(sender=router, ...)
            → allowedSwapper[pool][router] == true  ✓ (passes)
          → swap executes for unauthorized user

Result:
  - allowedSwapper[pool][unauthorizedUser] == false (never set)
  - But the guard passed because sender == router, which IS allowlisted.
  - Unauthorized user receives swap output; pool LPs bear the counterparty risk
    they intended to restrict.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** metric-core/contracts/ExtensionCalling.sol (L159-176)
```text
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
