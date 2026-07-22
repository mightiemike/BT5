### Title
`SwapAllowlistExtension` Gates the Router Address Instead of the End User, Allowing Any User to Bypass the Swap Allowlist via `MetricOmmSimpleRouter` - (File: `metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument passed by the pool, which is `msg.sender` of the `pool.swap(...)` call. When a user routes through `MetricOmmSimpleRouter`, the router is `msg.sender` of that call, so the extension checks whether the **router** is allowlisted — not the actual end user. If the pool admin allowlists the router (required for any router-mediated swap to succeed), every user on the network can bypass the per-user allowlist by routing through the public router.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` receives `sender` from the pool and uses `msg.sender` (the calling pool) as the mapping key:

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

`msg.sender` here is the pool (correct for the pool-keyed mapping). `sender` is whatever address the pool received as its own `msg.sender` when `swap` was called. In `MetricOmmPool.swap`, the pool passes `msg.sender` directly as `sender` to `_beforeSwap`:

```solidity
// MetricOmmPool.sol L230-240
_beforeSwap(
    msg.sender,   // <-- this is the router when routed
    recipient,
    ...
);
```

When a user calls `MetricOmmSimpleRouter.exactInput(...)`, the router calls `pool.swap(...)` on behalf of the user. The pool's `msg.sender` is the **router contract**, so `sender = router` is what the extension sees. The extension then evaluates `allowedSwapper[pool][router]`.

**Consequence — two broken states:**

1. **Allowlist bypass**: If the pool admin allowlists the router (the only way to permit any router-mediated swap), every unprivileged user can bypass the per-user allowlist by routing through `MetricOmmSimpleRouter`. The allowlist becomes a no-op for router paths.

2. **Allowlisted users locked out**: If the pool admin does NOT allowlist the router, individually allowlisted users cannot swap through the router at all, even though they are explicitly permitted. They must call `pool.swap` directly, which requires implementing `IMetricOmmSwapCallback` — not a standard user capability.

The `DepositAllowlistExtension` does **not** share this flaw because it checks `owner` (the position beneficiary explicitly passed by the caller), not `sender` (the payer/`msg.sender`):

```solidity
// DepositAllowlistExtension.sol L32-42
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
```

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict swaps to a curated set of addresses (e.g., KYC'd counterparties, protocol-controlled bots, or whitelisted market makers) can be fully bypassed by any user routing through `MetricOmmSimpleRouter`. The bypassing user executes swaps against the pool's liquidity, draining LP value through spread fees and oracle-price execution that the pool admin intended to restrict. This is a direct loss of LP principal control and breaks the core pool access-control invariant.

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is the primary public-facing swap interface for end users.
- The pool admin must allowlist the router to make the pool usable via the standard periphery; there is no other practical option for non-technical users.
- Once the router is allowlisted, the bypass requires zero special privileges — any EOA can call the router.
- The flaw is structural and requires no timing, flash loan, or oracle manipulation.

---

### Recommendation

The extension must check the **actual end user**, not the intermediate router. Two approaches:

1. **Pass the originating user through the router**: Have `MetricOmmSimpleRouter` accept a `swapper` parameter and pass it as `extensionData` or as the `recipient`-equivalent identity, and update `SwapAllowlistExtension` to decode and check that identity.

2. **Check `tx.origin` as a fallback** (weaker, but closes the router bypass): Replace `sender` with `tx.origin` in the allowlist lookup when `sender` is a known router. This is fragile and not recommended for production.

3. **Preferred — align with `DepositAllowlistExtension` pattern**: Introduce a `swapper` field that the pool explicitly tracks as the originating user (analogous to `owner` in liquidity operations), separate from the `msg.sender` payer, and gate on that field.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured.
  - Pool admin calls setAllowedToSwap(pool, router, true)  // allowlist the router
  - Pool admin does NOT allowlist attacker EOA

Attack:
  1. attacker (not allowlisted) calls MetricOmmSimpleRouter.exactInputSingle(...)
  2. Router calls pool.swap(recipient=attacker, ...) — msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[pool][router] → true ✓
  5. Swap executes; attacker receives tokens from the restricted pool

Result:
  - Attacker bypasses the per-user allowlist entirely.
  - Any user can repeat this; the allowlist is a dead letter for all router paths.
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
