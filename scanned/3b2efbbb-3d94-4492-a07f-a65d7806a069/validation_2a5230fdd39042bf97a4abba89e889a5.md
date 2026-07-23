### Title
Deposit Allowlist Bypassed via Owner/Sender Separation — (`metric-periphery/contracts/extensions/DepositAllowlistExtension.sol`)

---

### Summary

`DepositAllowlistExtension.beforeAddLiquidity` silently drops the `sender` argument and gates only on `owner`. Because `addLiquidity` explicitly permits `msg.sender ≠ owner`, any unprivileged, non-allowlisted address can bypass the configured deposit guard by nominating an allowlisted address as `owner`.

---

### Finding Description

`DepositAllowlistExtension.beforeAddLiquidity` receives two address arguments from the pool: `sender` (the actual payer / `msg.sender` of the pool call) and `owner` (the position beneficiary). The implementation discards `sender` entirely and checks only `owner`:

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol
function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
``` [1](#0-0) 

The pool's own interface documentation confirms that `addLiquidity` intentionally allows a different `msg.sender` from `owner`:

> *"Only `owner` may burn; `addLiquidity` may use a different `msg.sender` when `owner` is supplied, but removal is stricter."* [2](#0-1) 

`ExtensionCalling._beforeAddLiquidity` faithfully forwards both `sender` and `owner` to the hook:

```solidity
abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
``` [3](#0-2) 

The `LiquidityAdder` exposes a public entry-point that lets any caller specify an arbitrary `owner`:

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,   // caller-controlled
    uint80 salt,
    ...
)
``` [4](#0-3) 

---

### Impact Explanation

A non-allowlisted address Alice can call `LiquidityAdder.addLiquidityExactShares(pool, bob, salt, deltas, ...)` where `bob` is allowlisted. The extension sees `owner = bob`, the check passes, Alice's tokens are pulled, and Bob's position is credited. The configured deposit allowlist — the sole admission control for a curated pool — is fully bypassed by any unprivileged caller. This breaks the admin-boundary invariant: the pool admin intended to restrict which addresses may deposit, but the guard is keyed to the wrong actor.

---

### Likelihood Explanation

Likelihood is **high**. No special conditions are required: any non-allowlisted EOA or contract can call the public `LiquidityAdder` entry-point with any allowlisted address as `owner`. The allowlisted address need not cooperate; its address is public on-chain. The bypass works on every pool that uses `DepositAllowlistExtension` without `allowAllDepositors` being set.

---

### Recommendation

Gate on `sender` (the actual payer) rather than — or in addition to — `owner`. If the policy intent is to restrict who may pay tokens into the pool, `sender` is the correct identity to check:

```solidity
function beforeAddLiquidity(address sender, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external view override returns (bytes4)
{
    address pool_ = msg.sender;
    if (!allowAllDepositors[pool_]
        && !allowedDepositor[pool_][sender]   // gate the payer
        && !allowedDepositor[pool_][owner])   // optionally also gate the beneficiary
    {
        revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
}
```

If the intent is only to restrict position ownership (not payment), rename the extension and document the distinction clearly so pool admins are not misled.

---

### Proof of Concept

1. Pool admin deploys a pool with `DepositAllowlistExtension` wired into `beforeAddLiquidity`.
2. Pool admin calls `setAllowedToDeposit(pool, bob, true)` — Bob is allowlisted; Alice is not.
3. Alice (not allowlisted) calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(
       pool,
       bob,          // owner — allowlisted
       salt,
       deltas,
       maxAmount0,
       maxAmount1,
       ""
   );
   ```
4. Pool calls `DepositAllowlistExtension.beforeAddLiquidity(alice, bob, salt, deltas, "")`.
5. Extension evaluates `allowedDepositor[pool][bob]` → `true`; no revert.
6. Alice's tokens are transferred to the pool; Bob's position is credited with LP shares.
7. Alice has deposited into a curated pool despite being explicitly excluded from the allowlist. [1](#0-0) 
<cite repo="Thankgoddavid56/2026-07-metric-dev-oyakhil-main--025" path="metric-periphery/contracts/

### Citations

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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L89-92)
```text
  /// @notice `removeLiquidity` caller is not the position owner.
  /// @dev Only `owner` may burn; `addLiquidity` may use a different `msg.sender` when `owner` is supplied, but removal is stricter.
  error NotPositionOwner();

```

**File:** metric-core/contracts/ExtensionCalling.sol (L95-99)
```text
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
  }
```

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L87-95)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable returns (uint256 amount0Added, uint256 amount1Added);
```
