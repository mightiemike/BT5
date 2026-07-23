### Title
`SwapAllowlistExtension` Checks Router Address Instead of Original User, Allowing Any User to Bypass the Per-Pool Swap Allowlist — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool, which is the pool's direct `msg.sender`. When a user routes through `MetricOmmSimpleRouter`, the router becomes the pool's `msg.sender`, so the extension checks the router's address rather than the original user's address. If the pool admin allowlists the router address (the only way to enable router-mediated swaps for any user), the per-user allowlist is completely bypassed and any unprivileged user can swap.

---

### Finding Description

`SwapAllowlistExtension.beforeSwap` performs the following check:

```solidity
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
``` [1](#0-0) 

Here `msg.sender` is the pool (correct — the pool calls the extension), and `sender` is the argument the pool forwarded. The pool's `_beforeSwap` dispatcher passes `sender` directly from the pool's own `swap` call:

```solidity
function _beforeSwap(
    address sender,
    ...
) internal {
    _callExtensionsInOrder(
        BEFORE_SWAP_ORDER,
        abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, ...))
    );
}
``` [2](#0-1) 

The pool's `swap` function sets `sender = msg.sender` — the direct caller of the pool. When a user calls `MetricOmmSimpleRouter.exactInput*`, the router calls `pool.swap(...)`, making the router the pool's `msg.sender`. The extension therefore checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

This creates an irreconcilable dilemma for the pool admin:

| Admin action | Effect |
|---|---|
| Allowlist individual user EOAs only | Allowlisted users **cannot** use the router; their swaps revert because `sender = router` is not allowlisted |
| Allowlist the router address | **Any** user can bypass the allowlist by routing through the router |

The `DepositAllowlistExtension` does not share this flaw — it correctly gates on `owner` (the position owner argument), which is independent of who the direct caller is:

```solidity
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    ...
}
``` [3](#0-2) 

The swap extension has no equivalent "original user" argument to check.

---

### Impact Explanation

A pool configured with `SwapAllowlistExtension` to restrict trading to a curated set of counterparties (e.g., KYC'd addresses, institutional partners, or whitelisted market makers) can be fully bypassed by any unprivileged user routing through `MetricOmmSimpleRouter`. The attacker can execute swaps against the pool's liquidity at oracle-anchored prices, draining LP value or front-running restricted price feeds that the allowlist was designed to protect. This is a direct loss of LP principal and a broken core pool invariant (access control on swap).

---

### Likelihood Explanation

- `MetricOmmSimpleRouter` is a public, permissionless periphery contract — any EOA or contract can call it.
- The router is the natural entry point for swaps; pool admins who want to enable router-mediated swaps for their allowlisted users have no choice but to allowlist the router address.
- No special knowledge or privileged access is required; the attacker only needs to call the public router.
- The `SwapAllowlistExtension` is a production extension in `metric-periphery/contracts/extensions/`, not a test mock. [4](#0-3) 

---

### Recommendation

The extension must resolve the original user's identity rather than trusting the `sender` argument blindly. Two sound approaches:

1. **Pass original caller through the router**: `MetricOmmSimpleRouter` should accept a `swapper` parameter (or use `msg.sender` at the router level) and forward it as part of `extensionData`. The `SwapAllowlistExtension` should decode the original user from `extensionData` and verify it, rather than using the `sender` argument.

2. **Check `sender` against a router registry and then verify the original user**: The extension can accept a signed or encoded original-user claim in `extensionData` when `sender` is a known router, and verify the claim against the allowlist.

The simplest correct fix is for the router to encode `msg.sender` (the original user) into `extensionData` and for the extension to decode and check that value when `sender` is a recognized router address.

---

### Proof of Concept

```
Setup:
  - Deploy pool with SwapAllowlistExtension configured
  - Admin calls setAllowedToSwap(pool, router, true)
    (to allow router-mediated swaps for their allowlisted users)
  - Admin does NOT call setAllowedToSwap(pool, attacker, true)

Attack:
  1. attacker (non-allowlisted EOA) calls:
       MetricOmmSimpleRouter.exactInputSingle(pool, zeroForOne, amount, ...)
  2. Router calls pool.swap(recipient=attacker, ...)
       → pool's msg.sender = router
  3. Pool calls _beforeSwap(sender=router, ...)
  4. SwapAllowlistExtension.beforeSwap checks:
       allowedSwapper[pool][router] → true  ✓ (passes!)
  5. Swap executes at oracle price — attacker receives token output
     despite never being allowlisted

Result:
  attacker successfully swaps against a pool whose allowlist
  was intended to block them; LP assets are transferred to
  an unauthorized counterparty.
``` [1](#0-0) [2](#0-1)

### Citations

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L9-15)
```text
/// @title SwapAllowlistExtension
/// @notice Gates `swap` by swapper address, per pool.
contract SwapAllowlistExtension is BaseMetricExtension, ISwapAllowlistExtension {
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;

  constructor(address factory_) BaseMetricExtension(factory_) {}
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
